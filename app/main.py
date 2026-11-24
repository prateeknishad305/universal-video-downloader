"""HTTP API and static hosting for the video downloader."""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import yt_dlp
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config, telegram_bot, ytdlp_client
from .jobs import JobManager
from .models import AnalyzeRequest, BatchJobRequest, JobRequest
from .storage import build_store
from .ytdlp_client import CancelledError, UnsupportedSiteError

log = logging.getLogger(__name__)

store = build_store(config.MONGO_URI, config.MONGO_DB, config.MONGO_COLLECTION)
manager = JobManager(store=store)


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Start the optional Telegram bot and clean up on shutdown."""
    application.state.telegram = None
    if config.TELEGRAM_BOT_TOKEN:
        try:
            application.state.telegram = await telegram_bot.start_bot(
                manager, web_url=config.PUBLIC_URL
            )
        except Exception:  # pragma: no cover - the web app must still start
            log.exception("The Telegram bot could not be started")
    try:
        yield
    finally:
        await telegram_bot.stop_bot(getattr(application.state, "telegram", None))
        try:
            store.close()
        except Exception:  # pragma: no cover
            pass


app = FastAPI(
    title="Universal Video Downloader",
    description="Download video and audio from 1,800+ sites via yt-dlp.",
    version="1.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def token_guard(request: Request, call_next):
    """Require ``API_TOKEN`` on API routes when one is configured."""
    if config.API_TOKEN and request.url.path.startswith("/api/"):
        supplied = request.headers.get("x-api-token") or request.query_params.get("token")
        if supplied != config.API_TOKEN:
            return JSONResponse(
                {"detail": "Invalid or missing API token."}, status_code=401
            )
    return await call_next(request)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _analysis_error(exc: Exception) -> HTTPException:
    if isinstance(exc, UnsupportedSiteError):
        return HTTPException(status_code=400, detail=str(exc))
    message = str(exc) or exc.__class__.__name__
    return HTTPException(status_code=400, detail=message)


def _quality_lookup(analysis: Dict[str, Any], quality_id: str) -> Dict[str, Any]:
    qualities = analysis.get("qualities") or []
    for quality in qualities:
        if quality.get("id") == quality_id:
            return quality
    fallback = next((q for q in qualities if q.get("id") == "best"), None)
    if fallback:
        return fallback
    return {
        "id": "best",
        "label": "Best available",
        "selector": "best",
        "kind": "combined",
        "requires_merge": False,
    }


# --------------------------------------------------------------------------- #
# routes
# --------------------------------------------------------------------------- #


@app.get("/api/health")
async def health() -> Dict[str, Any]:
    import shutil

    return {
        "ok": True,
        "yt_dlp_version": yt_dlp.version.__version__,
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ffmpeg_path": shutil.which("ffmpeg"),
        "max_concurrent_jobs": config.MAX_CONCURRENT_JOBS,
        "download_dir": str(config.DOWNLOAD_DIR),
        "auth_required": bool(config.API_TOKEN),
        "storage": "mongodb" if config.MONGO_URI else "memory",
        "telegram": bool(config.TELEGRAM_BOT_TOKEN),
        "impersonation": config.IMPERSONATION_AVAILABLE,
    }


@app.get("/api/sites")
async def sites() -> Dict[str, Any]:
    """List every site plugin the installed yt-dlp supports."""
    names = await asyncio.to_thread(ytdlp_client.supported_extractors)
    return {"count": len(names), "sites": names}


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest) -> Dict[str, Any]:
    """Inspect a URL and return the media/format information for it."""
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="A URL is required.")

    try:
        analysis = await asyncio.to_thread(manager.analyse, url)
    except UnsupportedSiteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise _analysis_error(exc) from exc

    plan = dict(analysis)
    plan["_created"] = time.time()
    plan["url"] = url
    plan_id = manager.store_plan(plan)
    analysis["plan_id"] = plan_id
    return analysis


@app.post("/api/jobs")
async def create_job(req: JobRequest) -> Dict[str, Any]:
    """Queue a download and start it immediately."""
    url = req.url.strip()

    plan = manager.get_plan(req.plan_id) if req.plan_id else None
    if plan and plan.get("url") == url:
        analysis: Dict[str, Any] = plan
    else:
        try:
            analysis = await asyncio.to_thread(manager.analyse, url)
        except Exception as exc:  # noqa: BLE001
            raise _analysis_error(exc) from exc

    if analysis.get("kind") == "playlist":
        raise HTTPException(
            status_code=400,
            detail="This link is a playlist. Select the videos you want and download them individually.",
        )

    job = manager.create_from_analysis(
        url,
        analysis,
        quality_id=req.quality_id,
        mode=req.mode,
        subtitles=bool(req.subtitles),
    )
    return job.to_dict()


@app.post("/api/jobs/batch")
async def create_batch(req: BatchJobRequest) -> Dict[str, Any]:
    """Queue several downloads at once."""
    created: List[Dict[str, Any]] = []
    failed: List[Dict[str, str]] = []
    for item in req.items:
        try:
            job = await create_job(item)
            created.append(job)
        except HTTPException as exc:
            failed.append({"url": item.url, "error": str(exc.detail)})
    return {"created": created, "failed": failed}


@app.get("/api/jobs")
async def list_jobs() -> Dict[str, Any]:
    return {"jobs": [job.to_dict() for job in manager.all()]}


@app.get("/api/history")
async def history(limit: int = 50) -> Dict[str, Any]:
    """Previously recorded downloads (MongoDB when configured)."""
    return {"history": manager.history(limit=limit)}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> Dict[str, Any]:
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job.to_dict()


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request) -> StreamingResponse:
    """Server-sent events stream with live job progress."""
    if manager.get(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    async def event_stream():
        last_version = -1
        while True:
            if await request.is_disconnected():
                break
            job = manager.get(job_id)
            if job is None:
                break
            if job.version != last_version:
                last_version = job.version
                payload = json.dumps(job.to_dict())
                yield f"data: {payload}\n\n"
                if job.status in {"completed", "error", "cancelled"}:
                    break
            else:
                # keep-alive so proxies do not close an idle stream
                yield ": ping\n\n"
            await asyncio.sleep(0.4)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> Dict[str, Any]:
    if manager.get(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    manager.cancel(job_id)
    return {"ok": True}


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str, delete_file: bool = False) -> Dict[str, Any]:
    if not manager.remove(job_id, delete_file=delete_file):
        raise HTTPException(status_code=404, detail="Job not found.")
    return {"ok": True}


@app.post("/api/clear-finished")
async def clear_finished() -> Dict[str, Any]:
    return {"removed": manager.clear_finished()}


@app.get("/api/jobs/{job_id}/file")
async def download_file(job_id: str):
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status != "completed" or not job.filepath:
        raise HTTPException(status_code=409, detail="This job has no finished file.")

    path = Path(job.filepath)
    try:
        resolved = path.resolve()
        allowed = config.DOWNLOAD_DIR.resolve()
        if allowed not in resolved.parents and resolved != allowed:
            raise HTTPException(status_code=403, detail="File is outside the download folder.")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if not path.exists():
        raise HTTPException(status_code=410, detail="The downloaded file no longer exists.")

    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
        headers={"Content-Disposition": f'attachment; filename="{path.name}"'},
    )


# --------------------------------------------------------------------------- #
# static site
# --------------------------------------------------------------------------- #

app.mount("/", StaticFiles(directory=str(config.STATIC_DIR), html=True), name="static")
