"""Offline test suite.

The tests never touch the network: yt-dlp is replaced with deterministic
stubs so the whole API surface can be exercised quickly and repeatably.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, ytdlp_client  # noqa: E402
from app.main import app  # noqa: E402


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DOWNLOAD_DIR", tmp_path / "downloads")
    (tmp_path / "downloads").mkdir(parents=True, exist_ok=True)
    with TestClient(app) as test_client:
        yield test_client


VIDEO_ANALYSIS = {
    "kind": "video",
    "id": "abc123",
    "title": "Example Video",
    "uploader": "Example Channel",
    "thumbnail": "https://img.example.com/thumb.jpg",
    "duration": 213,
    "is_live": False,
    "webpage_url": "https://example.com/watch?v=abc123",
    "extractor": "Example",
    "formats": [
        {"format_id": "137", "ext": "mp4", "height": 1080, "fps": 30, "vcodec": "avc1",
         "acodec": "none", "filesize": 52428800, "tbr": 4000, "protocol": "https"},
        {"format_id": "140", "ext": "m4a", "height": None, "fps": None, "vcodec": "none",
         "acodec": "mp4a", "filesize": 3145728, "tbr": 128, "protocol": "https"},
    ],
    "qualities": [
        {"id": "best", "label": "Best quality (original file, no re-encode)", "hint": "x",
         "selector": "best", "kind": "combined", "height": 1080, "hdr": False,
         "size": 52428800, "requires_merge": False},
        {"id": "v1080", "label": "1080p", "hint": "y", "selector": "bestvideo[height<=1080]+bestaudio/best",
         "kind": "merged", "height": 1080, "hdr": False, "size": 55574528, "requires_merge": True},
        {"id": "audio_mp3", "label": "Audio only - MP3 (192 kbps)", "hint": "z",
         "selector": "bestaudio/best", "kind": "audio", "height": None, "hdr": False,
         "size": 3145728, "requires_merge": False},
    ],
    "playlist_count": 0,
}

PLAYLIST_ANALYSIS = {
    "kind": "playlist",
    "id": "pl1",
    "title": "My Playlist",
    "uploader": "Example Channel",
    "thumbnail": None,
    "webpage_url": "https://example.com/playlist?list=pl1",
    "extractor": "Example",
    "playlist_count": 2,
    "entries": [
        {"id": "a", "title": "First clip", "url": "https://example.com/watch?v=a",
         "duration": 30, "thumbnail": None, "uploader": "Example Channel"},
        {"id": "b", "title": "Second clip", "url": "https://example.com/watch?v=b",
         "duration": 60, "thumbnail": None, "uploader": "Example Channel"},
    ],
}


# --------------------------------------------------------------------------- #
# unit tests
# --------------------------------------------------------------------------- #


def test_safe_filename_strips_path_traversal():
    assert "/" not in ytdlp_client.safe_filename("../../etc/passwd")
    assert ytdlp_client.safe_filename("")  == "download"
    assert ytdlp_client.safe_filename("a/b:c*d?e") == "a b c d e"


def test_quality_ladder_groups_resolutions_and_audio():
    formats = [
        {"height": 1080, "vcodec": "avc1", "acodec": "none", "filesize": 1000},
        {"height": 720, "vcodec": "avc1", "acodec": "none", "filesize": 500},
        {"height": 360, "vcodec": "avc1", "acodec": "none", "filesize": 200},
        {"height": None, "vcodec": "none", "acodec": "mp4a", "filesize": 50},
    ]
    ladder = ytdlp_client.build_quality_ladder(formats)
    ids = [q["id"] for q in ladder]
    assert ids[0] == "best"
    assert "v1080" in ids and "v720" in ids and "v360" in ids
    assert "audio_mp3" in ids and "audio_original" in ids
    v1080 = next(q for q in ladder if q["id"] == "v1080")
    assert v1080["requires_merge"] is True
    assert v1080["size"] == 1050  # video + audio estimate


def test_quality_ladder_marks_hdr():
    formats = [{"height": 2160, "vcodec": "vp9", "acodec": "none",
                "dynamic_range": "HDR", "filesize": 10}]
    ladder = ytdlp_client.build_quality_ladder(formats)
    v2160 = next(q for q in ladder if q["id"] == "v2160")
    assert v2160["hdr"] is True
    assert "HDR" in v2160["label"]


# --------------------------------------------------------------------------- #
# API tests
# --------------------------------------------------------------------------- #


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert "yt_dlp_version" in body


def test_index_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Universal Downloader" in response.text


def test_analyze_video(client, monkeypatch):
    monkeypatch.setattr(ytdlp_client, "analyze", lambda url: dict(VIDEO_ANALYSIS))
    response = client.post("/api/analyze", json={"url": "https://example.com/watch?v=abc123"})
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "video"
    assert body["plan_id"]
    assert {q["id"] for q in body["qualities"]} >= {"best", "v1080", "audio_mp3"}


def test_analyze_playlist(client, monkeypatch):
    monkeypatch.setattr(ytdlp_client, "analyze", lambda url: dict(PLAYLIST_ANALYSIS))
    response = client.post("/api/analyze", json={"url": "https://example.com/playlist?list=pl1"})
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "playlist"
    assert body["playlist_count"] == 2
    assert len(body["entries"]) == 2


def test_analyze_unsupported_site_returns_400(client, monkeypatch):
    def boom(_url):
        raise ytdlp_client.UnsupportedSiteError("No downloader plugin recognises that URL.")

    monkeypatch.setattr(ytdlp_client, "analyze", boom)
    response = client.post("/api/analyze", json={"url": "https://not-a-real-site.example/page"})
    assert response.status_code == 400
    assert "No downloader plugin" in response.json()["detail"]


def test_analyze_requires_a_url(client):
    assert client.post("/api/analyze", json={"url": ""}).status_code == 422


def test_job_runs_to_completion_and_serves_file(client, monkeypatch):
    monkeypatch.setattr(ytdlp_client, "analyze", lambda url: dict(VIDEO_ANALYSIS))

    def fake_download(url, *, outtmpl, selector, mode="video", subtitles=False,
                      on_progress=None, on_log=None, should_cancel=None):
        assert selector  # a real selector is always provided
        target = Path(outtmpl.replace(".%(ext)s", ".mp4"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fake-video-bytes")
        if on_progress:
            on_progress({"downloaded_bytes": 100, "total_bytes": 100,
                         "speed": 1000.0, "eta": 1})
        return target

    monkeypatch.setattr("app.jobs.download_media", fake_download)

    response = client.post("/api/jobs", json={
        "url": "https://example.com/watch?v=abc123",
        "quality_id": "v1080",
        "mode": "video",
    })
    assert response.status_code == 200
    job_id = response.json()["id"]

    deadline = time.time() + 15
    job = {}
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"completed", "error", "cancelled"}:
            break
        time.sleep(0.1)

    assert job["status"] == "completed", job
    assert job["progress"] == 100.0
    assert job["filename"].endswith(".mp4")

    file_response = client.get(f"/api/jobs/{job_id}/file")
    assert file_response.status_code == 200
    assert file_response.content == b"fake-video-bytes"
    assert "attachment" in file_response.headers["content-disposition"]


def test_playlist_url_cannot_be_downloaded_directly(client, monkeypatch):
    monkeypatch.setattr(ytdlp_client, "analyze", lambda url: dict(PLAYLIST_ANALYSIS))
    response = client.post("/api/jobs", json={"url": "https://example.com/playlist?list=pl1"})
    assert response.status_code == 400
    assert "playlist" in response.json()["detail"].lower()


def test_file_endpoint_rejects_unfinished_job(client, monkeypatch):
    monkeypatch.setattr(ytdlp_client, "analyze", lambda url: dict(VIDEO_ANALYSIS))

    def slow_download(url, *, outtmpl, selector, mode="video", subtitles=False,
                      on_progress=None, on_log=None, should_cancel=None):
        for _ in range(100):
            time.sleep(0.05)
        raise AssertionError("should not finish")

    monkeypatch.setattr("app.jobs.download_media", slow_download)

    job_id = client.post("/api/jobs", json={"url": "https://example.com/watch?v=abc123"}).json()["id"]
    response = client.get(f"/api/jobs/{job_id}/file")
    assert response.status_code == 409
    client.post(f"/api/jobs/{job_id}/cancel")


def test_cancel_marks_job_cancelled(client, monkeypatch):
    monkeypatch.setattr(ytdlp_client, "analyze", lambda url: dict(VIDEO_ANALYSIS))

    def slow_download(url, *, outtmpl, selector, mode="video", subtitles=False,
                      on_progress=None, on_log=None, should_cancel=None):
        for _ in range(100):
            if should_cancel and should_cancel():
                raise ytdlp_client.CancelledError("Download cancelled by the user.")
            time.sleep(0.05)
        raise AssertionError("should have been cancelled")

    monkeypatch.setattr("app.jobs.download_media", slow_download)

    job_id = client.post("/api/jobs", json={"url": "https://example.com/watch?v=abc123"}).json()["id"]
    time.sleep(0.2)
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200

    deadline = time.time() + 15
    job = {}
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"completed", "error", "cancelled"}:
            break
        time.sleep(0.1)
    assert job["status"] == "cancelled"


def test_token_guard_blocks_api_without_token(client, monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "secret-token")
    assert client.get("/api/jobs").status_code == 401
    assert client.get("/api/jobs", headers={"X-API-Token": "secret-token"}).status_code == 200


def test_detect_extractor_finds_known_sites():
    from app.ytdlp_client import detect_extractor, supported_extractors

    assert detect_extractor("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "youtube"
    assert detect_extractor("https://vimeo.com/123456789") == "vimeo"
    assert len(supported_extractors()) > 1000


def test_supported_sites_endpoint_lists_extractors(client):
    response = client.get("/api/sites")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] > 1000
    assert "youtube" in body["sites"]
