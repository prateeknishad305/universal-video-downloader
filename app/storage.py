"""Optional persistent storage for the job history.

The downloader keeps running jobs in memory so that progress can be streamed
at 60 fps, but it is nice to keep a record of what was downloaded.  When
``MONGO_URI`` is configured every job snapshot is mirrored into MongoDB; when
it is not, a tiny in-process store is used and nothing else changes.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

TERMINAL_STATES = ("completed", "error", "cancelled")


class JobStore:
    """Interface for persisting job snapshots."""

    def save(self, snapshot: Dict[str, Any]) -> None:
        raise NotImplementedError

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def list(self, limit: int = 100) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def delete(self, job_id: str) -> bool:
        raise NotImplementedError

    def clear_finished(self) -> int:
        raise NotImplementedError

    def close(self) -> None:
        """Release any resources held by the store."""


class MemoryJobStore(JobStore):
    """Default store - keeps a bounded history in the process itself."""

    def __init__(self, max_items: int = 500) -> None:
        self._items: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._max_items = max_items

    def save(self, snapshot: Dict[str, Any]) -> None:
        job_id = snapshot.get("id")
        if not job_id:
            return
        with self._lock:
            self._items[job_id] = dict(snapshot)
            if len(self._items) > self._max_items:
                ordered = sorted(
                    self._items.values(), key=lambda item: item.get("created_at") or 0
                )
                for stale in ordered[: len(self._items) - self._max_items]:
                    self._items.pop(stale.get("id"), None)

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            item = self._items.get(job_id)
            return dict(item) if item else None

    def list(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            items = sorted(
                self._items.values(), key=lambda item: item.get("created_at") or 0, reverse=True
            )
            return [dict(item) for item in items[:limit]]

    def delete(self, job_id: str) -> bool:
        with self._lock:
            return self._items.pop(job_id, None) is not None

    def clear_finished(self) -> int:
        with self._lock:
            stale = [
                key for key, value in self._items.items() if value.get("status") in TERMINAL_STATES
            ]
            for key in stale:
                self._items.pop(key, None)
            return len(stale)


class MongoJobStore(JobStore):
    """MongoDB backed store.

    The PyMongo driver is synchronous, so the job manager calls into this
    store from a worker thread and never blocks the event loop.
    """

    def __init__(
        self,
        uri: str,
        database: str = "universal_downloader",
        collection: str = "jobs",
        timeout_ms: int = 5000,
        client: Optional[Any] = None,
    ) -> None:
        self._client = client
        if self._client is None:
            from pymongo import ASCENDING, MongoClient

            self._client = MongoClient(
                uri,
                serverSelectionTimeoutMS=timeout_ms,
                connectTimeoutMS=timeout_ms,
                socketTimeoutMS=timeout_ms,
            )
            # Fail fast when the server is unreachable.
            self._client.admin.command("ping")
        else:
            from pymongo import ASCENDING

        self._collection = self._client[database][collection]
        self._collection.create_index([("created_at", ASCENDING)])
        self._collection.create_index("id", unique=True)

    def save(self, snapshot: Dict[str, Any]) -> None:
        job_id = snapshot.get("id")
        if not job_id:
            return
        self._collection.update_one({"id": job_id}, {"$set": snapshot}, upsert=True)

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        document = self._collection.find_one({"id": job_id}, {"_id": 0})
        return dict(document) if document else None

    def list(self, limit: int = 100) -> List[Dict[str, Any]]:
        cursor = self._collection.find({}, {"_id": 0}).sort("created_at", -1).limit(limit)
        return [dict(document) for document in cursor]

    def delete(self, job_id: str) -> bool:
        result = self._collection.delete_one({"id": job_id})
        return bool(result.deleted_count)

    def clear_finished(self) -> int:
        result = self._collection.delete_many({"status": {"$in": list(TERMINAL_STATES)}})
        return int(result.deleted_count)

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # pragma: no cover - best effort
            pass


def build_store(
    uri: Optional[str] = None,
    database: str = "universal_downloader",
    collection: str = "jobs",
    timeout_ms: int = 5000,
) -> JobStore:
    """Return a MongoDB store when *uri* is usable, otherwise a memory store."""
    if not uri:
        return MemoryJobStore()
    try:
        store = MongoJobStore(
            uri, database=database, collection=collection, timeout_ms=timeout_ms
        )
        log.info("Job history is stored in MongoDB (%s)", database)
        return store
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "MongoDB is unavailable (%s) - falling back to in-memory job history", exc
        )
        return MemoryJobStore()
