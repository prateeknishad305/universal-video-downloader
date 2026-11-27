"""In-memory job queue that tracks every download from start to finish."""

from __future__ import annotations

import asyncio
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config, ytdlp_client
from .storage import JobStore, MemoryJobStore
from .ytdlp_client import (
    CancelledError,
    download_media,
    remove_partial_files,
    safe_filename,
)

TERMINAL_STATES = {"completed", "error", "cancelled"}


def _disk_free_mb() -> int:
    """Free space on the download disk in MB, 0 when it cannot be read."""
    try:
        return shutil.disk_usage(str(config.DOWNLOAD_DIR)).free // (1024 * 1024)
    except Exception:
        return 0


def _disk_space_problem(needed_mb: int = 0) -> str:
    """Complain when the download disk is too full for a new file."""
    required = int(config.MIN_FREE_DISK_MB) + max(0, needed_mb)
    if required <= 0:
        return ""
    free_mb = _disk_free_mb()
    if free_mb < required:
        return (
            f"The download disk has only {free_mb} MB free, "
            f"at least {required} MB are needed."
        )
    return ""


@dataclass
class Job:
    """A single download in flight (or already finished)."""

    id: str
    url: str
    title: str = ""
    quality_id: str = "best"
    quality_label: str = ""
    mode: str = "video"
    status: str = "queued"
    stage: str = "Queued"
    progress: float = 0.0
    downloaded_bytes: int = 0
    total_bytes: int = 0
    speed: Optional[float] = None
    eta: Optional[int] = None
    filename: Optional[str] = None
    filepath: Optional[str] = None
    error: Optional[str] = None
    thumbnail: Optional[str] = None
    duration: Optional[float] = None
    uploader: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    version: int = 0
    cancel_requested: bool = False
    logs: List[str] = field(default_factory=list)

    # -- mutations -------------------------------------------------------- #
    def touch(self) -> None:
        self.updated_at = time.time()
        self.version += 1

    def add_log(self, level: str, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.logs.append(f"[{stamp}] {level.upper()}: {message}")
        del self.logs[:-200]
        self.touch()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "quality_id": self.quality_id,
            "quality_label": self.quality_label,
            "mode": self.mode,
            "status": self.status,
            "stage": self.stage,
            "progress": round(self.progress, 2),
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "speed": self.speed,
            "eta": self.eta,
            "filename": self.filename,
            "has_file": bool(self.filepath),
            "error": self.error,
            "thumbnail": self.thumbnail,
            "duration": self.duration,
            "uploader": self.uploader,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "cancel_requested": self.cancel_requested,
            "logs": self.logs[-25:],
        }


class JobManager:
    """Creates, runs and tracks download jobs.

    Downloads run in worker threads (yt-dlp is blocking) while a semaphore
    caps how many run at once.
    """

    def __init__(
        self,
        max_concurrent: int = config.MAX_CONCURRENT_JOBS,
        store: Optional[JobStore] = None,
    ) -> None:
        self._jobs: Dict[str, Job] = {}
        self._plans: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._max_concurrent = max(1, int(max_concurrent))
        self._tasks: Dict[str, asyncio.Task] = {}
        self._store: JobStore = store or MemoryJobStore()

    # -- persistence ------------------------------------------------------ #
    def _persist(self, job: Job) -> None:
        """Mirror a job snapshot into the history store (never blocks)."""
        snapshot = job.to_dict()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        try:
            if loop is not None:
                loop.create_task(asyncio.to_thread(self._store.save, snapshot))
            else:
                self._store.save(snapshot)
        except Exception:  # pragma: no cover - storage must never break a job
            pass

    def history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Previously recorded jobs, newest first."""
        try:
            return self._store.list(limit=limit)
        except Exception:  # pragma: no cover
            return []

    # -- lifecycle -------------------------------------------------------- #
    def _sem(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._max_concurrent)
        return self._semaphore

    # -- analysis --------------------------------------------------------- #
    def analyse(self, url: str) -> Dict[str, Any]:
        """Probe a URL and return the analysis (title, formats, qualities)."""
        return ytdlp_client.analyze(url)

    def create_from_analysis(
        self,
        url: str,
        analysis: Dict[str, Any],
        *,
        quality_id: str = "best",
        mode: str = "video",
        subtitles: bool = False,
    ) -> Job:
        """Create and start a job from an existing analysis."""
        qualities = analysis.get("qualities") or []
        quality = next((q for q in qualities if q.get("id") == quality_id), None)
        if quality is None:
            quality = next((q for q in qualities if q.get("id") == "best"), None)
        if quality is None:
            quality = {
                "id": "best",
                "label": "Best available",
                "selector": "bestvideo+bestaudio/best",
                "kind": "combined",
                "requires_merge": True,
            }

        effective_mode = mode
        if quality.get("kind") == "audio":
            effective_mode = "audio"
        elif quality.get("id") == "audio_original":
            effective_mode = "audio_original"

        job = self.create(
            url,
            quality_id=quality.get("id", "best"),
            mode=effective_mode,
            title=analysis.get("title") or "download",
            quality_label=quality.get("label", ""),
            thumbnail=analysis.get("thumbnail"),
            duration=analysis.get("duration"),
            uploader=analysis.get("uploader"),
        )
        self.start(job, quality.get("selector", "best"), subtitles=subtitles)
        return job

    # -- plans ------------------------------------------------------------ #
    def store_plan(self, plan: Dict[str, Any], ttl: float = 3600.0) -> str:
        """Remember an analysis result and return a short plan id."""
        plan_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._prune_plans(ttl)
            self._plans[plan_id] = plan
        return plan_id

    def get_plan(self, plan_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._plans.get(plan_id)

    def _prune_plans(self, ttl: float) -> None:
        now = time.time()
        stale = [pid for pid, plan in self._plans.items() if now - plan.get("_created", now) > ttl]
        for pid in stale:
            self._plans.pop(pid, None)

    # -- jobs ------------------------------------------------------------- #
    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def all(self) -> List[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def create(
        self,
        url: str,
        *,
        quality_id: str = "best",
        mode: str = "video",
        title: str = "",
        quality_label: str = "",
        thumbnail: Optional[str] = None,
        duration: Optional[float] = None,
        uploader: Optional[str] = None,
    ) -> Job:
        job = Job(
            id=uuid.uuid4().hex[:12],
            url=url,
            title=title or "Preparing...",
            quality_id=quality_id,
            quality_label=quality_label,
            mode=mode,
            thumbnail=thumbnail,
            duration=duration,
            uploader=uploader,
        )
        with self._lock:
            self._jobs[job.id] = job
            self._trim_jobs()
        self._persist(job)
        return job

    def _trim_jobs(self) -> None:
        """Drop the oldest finished jobs so the memory stays bounded."""
        limit = max(10, int(config.MAX_HISTORY_JOBS))
        if len(self._jobs) <= limit:
            return
        finished = [
            job for job in self._jobs.values() if job.status in TERMINAL_STATES
        ]
        finished.sort(key=lambda job: job.created_at)
        while len(self._jobs) > limit and finished:
            oldest = finished.pop(0)
            self._jobs.pop(oldest.id, None)

    def start(self, job: Job, selector: str, subtitles: bool = False) -> None:
        """Schedule the download of *job*."""
        with self._lock:
            self._tasks[job.id] = asyncio.create_task(self._run(job, selector, subtitles))

    async def _run(self, job: Job, selector: str, subtitles: bool) -> None:
        async with self._sem():
            if job.cancel_requested:
                self._finish_cancelled(job)
                return

            stem = f"{job.id}_{safe_filename(job.title, 90)}"
            outtmpl = str(config.DOWNLOAD_DIR / f"{stem}.%(ext)s")

            def on_progress(data: Dict[str, Any]) -> None:
                with self._lock:
                    if data.get("phase") == "processing":
                        job.status = "processing"
                        job.stage = "Merging / converting formats"
                        job.progress = 99.0
                        job.speed = None
                        job.eta = None
                    else:
                        done = data.get("downloaded_bytes") or 0
                        total = data.get("total_bytes") or 0
                        job.downloaded_bytes = done
                        job.total_bytes = total
                        job.speed = data.get("speed")
                        job.eta = data.get("eta")
                        job.progress = (done / total * 100.0) if total else 0.0
                        job.stage = "Downloading"
                        if total and config.MIN_FREE_DISK_MB > 0:
                            free_mb = _disk_free_mb()
                            if free_mb < int(config.MIN_FREE_DISK_MB):
                                raise RuntimeError(
                                    f"The download disk ran out of space, "
                                    f"only {free_mb} MB were left."
                                )
                    job.touch()

            def on_log(level: str, message: str) -> None:
                with self._lock:
                    job.add_log(level, message)

            def should_cancel() -> bool:
                with self._lock:
                    return job.cancel_requested

            try:
                with self._lock:
                    job.status = "downloading"
                    job.stage = "Starting..."
                    job.touch()

                space_problem = _disk_space_problem()
                if space_problem:
                    raise RuntimeError(space_problem)

                path: Path = await asyncio.to_thread(
                    download_media,
                    job.url,
                    outtmpl=outtmpl,
                    selector=selector,
                    mode=job.mode,
                    subtitles=subtitles,
                    on_progress=on_progress,
                    on_log=on_log,
                    should_cancel=should_cancel,
                )

                with self._lock:
                    job.filepath = str(path)
                    job.filename = path.name
                    job.status = "completed"
                    job.stage = "Completed"
                    job.progress = 100.0
                    job.speed = None
                    job.eta = None
                    job.touch()
                    job.add_log("info", f"Saved to {path.name}")
                self._persist(job)

            except CancelledError:
                remove_partial_files(config.DOWNLOAD_DIR, stem)
                self._finish_cancelled(job)
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI
                remove_partial_files(config.DOWNLOAD_DIR, stem)
                with self._lock:
                    job.status = "error"
                    job.stage = "Failed"
                    job.error = str(exc) or exc.__class__.__name__
                    job.touch()
                    job.add_log("error", job.error)
                self._persist(job)

    def _finish_cancelled(self, job: Job) -> None:
        with self._lock:
            job.status = "cancelled"
            job.stage = "Cancelled"
            job.speed = None
            job.eta = None
            job.touch()
            job.add_log("warning", "Download cancelled.")
        self._persist(job)

    # -- control ---------------------------------------------------------- #
    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None or job.status in TERMINAL_STATES:
            return False
        with self._lock:
            job.cancel_requested = True
            job.stage = "Cancelling..."
            job.touch()
        self._persist(job)
        return True

    def remove(self, job_id: str, delete_file: bool = False) -> bool:
        with self._lock:
            job = self._jobs.pop(job_id, None)
            task = self._tasks.pop(job_id, None)
        if job is None:
            return False
        if job.status not in TERMINAL_STATES:
            self.cancel(job_id)
        if task is not None and not task.done():
            task.cancel()
        if delete_file and job.filepath:
            try:
                Path(job.filepath).unlink(missing_ok=True)
            except OSError:
                pass
        try:
            self._store.delete(job_id)
        except Exception:  # pragma: no cover
            pass
        return True

    def clear_finished(self) -> int:
        removed = 0
        for job in self.all():
            if job.status in TERMINAL_STATES:
                if self.remove(job.id):
                    removed += 1
        return removed
