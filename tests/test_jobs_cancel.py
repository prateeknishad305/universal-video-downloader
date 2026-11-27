"""Tests for the cancel and delete behaviour of the job queue."""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import jobs as jobs_module  # noqa: E402
from app.jobs import JobManager  # noqa: E402
from app.storage import MemoryJobStore  # noqa: E402
from app.ytdlp_client import CancelledError  # noqa: E402


@pytest.fixture()
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module.config, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(jobs_module.config, "MIN_FREE_DISK_MB", 0)
    return JobManager(store=MemoryJobStore())


def test_delete_of_a_running_job_stops_the_worker(manager, tmp_path, monkeypatch):
    """A deleted job must not keep downloading in the background."""
    started = threading.Event()
    stopped = threading.Event()
    partials = []

    def fake_download(url, *, outtmpl, selector, mode="video", subtitles=False,
                      on_progress=None, on_log=None, should_cancel=None):
        partial = Path(outtmpl.replace(".%(ext)s", ".mp4.part"))
        partials.append(partial)
        started.set()
        while True:
            if should_cancel and should_cancel():
                stopped.set()
                raise CancelledError("Download cancelled by the user.")
            partial.write_bytes(b"x" * 1024)
            threading.Event().wait(0.01)

    monkeypatch.setattr(jobs_module, "download_media", fake_download)

    async def scenario():
        job = manager.create("https://example.com/movie", title="movie")
        manager.start(job, "best")
        task = manager._tasks[job.id]
        assert await asyncio.to_thread(started.wait, 5)
        assert manager.remove(job.id) is True
        await task
        return job

    job = asyncio.run(scenario())

    assert stopped.is_set(), "the worker kept running after the job was deleted"
    assert manager.get(job.id) is None
    assert partials and not partials[0].exists(), "the partial file was left behind"


def test_cancel_after_the_file_was_written_removes_it(manager, tmp_path, monkeypatch):
    """A cancel that arrives late must not leave a finished file behind."""
    finished = tmp_path / "movie.mp4"

    def fake_download(url, *, outtmpl, selector, mode="video", subtitles=False,
                      on_progress=None, on_log=None, should_cancel=None):
        finished.write_bytes(b"video")
        if on_log:
            on_log("info", "done")
        return finished

    monkeypatch.setattr(jobs_module, "download_media", fake_download)

    async def scenario():
        job = manager.create("https://example.com/movie", title="movie")
        manager.start(job, "best")
        task = manager._tasks[job.id]
        await asyncio.sleep(0)
        manager.cancel(job.id)
        await task
        return job

    job = asyncio.run(scenario())

    assert job.status == "cancelled"
    assert not finished.exists(), "the finished file was kept after a late cancel"


def test_cancelled_job_is_not_written_to_the_history(manager, monkeypatch):
    """A removed job must not come back through the history store."""
    job = manager.create("https://example.com/movie", title="movie")
    assert manager.remove(job.id) is True
    manager._persist(job)
    assert all(entry.get("id") != job.id for entry in manager.history())


def test_leftover_files_of_a_cancelled_download_are_removed(tmp_path):
    from app import ytdlp_client

    stem = "movie"
    leftovers = [
        tmp_path / "movie.part",
        tmp_path / "movie.mp4.part",
        tmp_path / "movie.part-Frag0",
        tmp_path / "movie.f137.mp4",
        tmp_path / "movie.temp.mp4",
        tmp_path / "movie.mp4.ytdl",
    ]
    for path in leftovers:
        path.write_bytes(b"x")

    found = ytdlp_client.find_partial_files(tmp_path, stem)
    assert set(found) == set(leftovers)

    keep = tmp_path / "movie.f137.mp4"
    ytdlp_client.remove_partial_files(tmp_path, stem, keep=keep)
    assert keep.exists()
    assert not (tmp_path / "movie.part").exists()
    assert not (tmp_path / "movie.temp.mp4").exists()
    assert not (tmp_path / "movie.mp4.ytdl").exists()
