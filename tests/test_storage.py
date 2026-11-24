"""Tests for the optional job-history store."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.storage import MemoryJobStore, build_store  # noqa: E402


def _snapshot(job_id: str, status: str = "completed", created: float = 1.0):
    return {
        "id": job_id,
        "status": status,
        "title": f"job {job_id}",
        "created_at": created,
    }


def test_memory_store_roundtrip():
    store = MemoryJobStore()
    store.save(_snapshot("a", created=1.0))
    store.save(_snapshot("b", status="downloading", created=2.0))

    assert store.get("a")["title"] == "job a"
    assert store.get("missing") is None
    assert len(store.list()) == 2
    assert store.list()[0]["id"] == "b"  # newest first

    assert store.delete("a") is True
    assert store.delete("a") is False
    assert len(store.list()) == 1


def test_memory_store_clear_finished():
    store = MemoryJobStore()
    store.save(_snapshot("a", status="completed"))
    store.save(_snapshot("b", status="error"))
    store.save(_snapshot("c", status="downloading"))
    assert store.clear_finished() == 2
    assert [item["id"] for item in store.list()] == ["c"]


def test_memory_store_is_bounded():
    store = MemoryJobStore(max_items=3)
    for index in range(6):
        store.save(_snapshot(str(index), created=float(index)))
    assert len(store.list()) == 3
    # the newest snapshots survive
    assert {item["id"] for item in store.list()} == {"3", "4", "5"}


def test_build_store_without_uri_uses_memory():
    store = build_store(None)
    assert isinstance(store, MemoryJobStore)


def test_build_store_falls_back_when_mongo_is_unreachable():
    """An unreachable MongoDB must not stop the app from starting."""
    store = build_store("mongodb://127.0.0.1:1/unreachable", timeout_ms=1)
    assert isinstance(store, MemoryJobStore)


def test_job_manager_history_uses_the_store():
    from app.jobs import JobManager

    store = MemoryJobStore()
    manager = JobManager(store=store)
    job = manager.create("https://example.com/video", title="Example")
    manager._persist(job)

    history = manager.history()
    assert any(item["id"] == job.id for item in history)


def test_mongo_store_roundtrip():
    """The MongoDB code path, exercised against an in-memory emulator."""
    mongomock = pytest.importorskip("mongomock")

    from app.storage import MongoJobStore

    client = mongomock.MongoClient()
    store = MongoJobStore("mongodb://unused", client=client)

    store.save(_snapshot("m1", status="completed", created=1.0))
    store.save(_snapshot("m2", status="downloading", created=2.0))

    assert store.get("m1")["title"] == "job m1"
    assert store.get("nope") is None
    assert len(store.list()) == 2
    assert store.list()[0]["id"] == "m2"  # newest first

    assert store.delete("m1") is True
    assert len(store.list()) == 1

    # only the finished one is removed; the running job stays
    store.save(_snapshot("m3", status="error", created=3.0))
    assert store.clear_finished() == 1
    assert [item["id"] for item in store.list()] == ["m2"]


def test_mongo_store_upserts_the_same_job():
    mongomock = pytest.importorskip("mongomock")

    from app.storage import MongoJobStore

    client = mongomock.MongoClient()
    store = MongoJobStore("mongodb://unused", client=client)

    store.save(_snapshot("x", status="downloading", created=1.0))
    store.save(_snapshot("x", status="completed", created=1.0))
    assert len(store.list()) == 1
    assert store.get("x")["status"] == "completed"
