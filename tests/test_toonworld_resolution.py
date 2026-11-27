"""Offline tests for the toonworld resolution pipeline."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yt_dlp
import yt_dlp.networking.impersonate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import ytdlp_client  # noqa: E402
from app.sources import toonworld, toonworld_hosts  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8", errors="replace")


@pytest.fixture()
def episode_page(monkeypatch):
    body = read_fixture("episode_page.html")
    monkeypatch.setattr(toonworld.http, "fetch", lambda url, **_kw: (200, body, {}))
    return body


@pytest.fixture(autouse=True)
def no_browser(monkeypatch):
    """Keep the browser fallback out of the offline tests."""
    monkeypatch.setattr(toonworld, "_browser_links_for_sources", lambda *a, **kw: {})
    return None


def test_interstitial_exposes_the_shortener_and_the_host(monkeypatch):
    body = read_fixture("redirect_props_page.html")
    monkeypatch.setattr(toonworld.http, "fetch", lambda url, **_kw: (200, body, {}))
    info = toonworld.resolve_interstitial("abc123")
    assert info["error"] is None
    assert info["shortener"] == "https://shrinkme.click/RPFhm3l"
    assert info["host"] == "filepress"
    assert info["file_id"] == "0u04f8hzahkjgjci2s6xzckt"


@pytest.mark.parametrize(
    "url,host,file_id",
    [
        ("https://hubcloud.link/drive/abc123", "hubcloud", "abc123"),
        ("https://new4.filepress.lat/file/0123456789abcdef01234567", "filepress",
         "0123456789abcdef01234567"),
        ("https://new10.gdflix.io/file/xyz789", "gdflix", "xyz789"),
        ("https://mega.nz/file/abcdefgh#zyeamm5tdieoc1ff469dzejd2je57qhw1wdsj4de3t5",
         "mega", "abcdefgh#zyeamm5tdieoc1ff469dzejd2je57qhw1wdsj4de3t5"),
    ],
)
def test_host_id_from_url(url, host, file_id):
    assert toonworld.host_id_from_url(url) == (host, file_id)


def test_resolve_sources_uses_the_host_resolver(episode_page, monkeypatch):
    monkeypatch.setattr(toonworld, "validate_host_id", lambda host, file_id: None)
    monkeypatch.setattr(
        toonworld,
        "resolve_interstitial",
        lambda token, **_kw: {
            "destination": "https://shrinkme.click/RPFhm3l",
            "shortener": "https://shrinkme.click/RPFhm3l",
            "host": "filepress",
            "file_id": "0123456789abcdef01234567",
            "props": {},
            "error": None,
        },
    )
    monkeypatch.setattr(
        toonworld_hosts,
        "resolve",
        lambda host, file_id, **_kw: toonworld_hosts.HostResult(
            url="https://cdn.filepress.dev/e.mkv", state="ready", detail="resolved"
        ),
    )
    sources = toonworld.resolve_sources(
        "https://archive.toonworld4all.me/episode/naruto-shippuden-1x373", "480p-h264-8bit"
    )
    assert len(sources) == 4
    assert all(source["state"] == "ready" for source in sources)
    assert sources[0]["url"] == "https://cdn.filepress.dev/e.mkv"


def test_resolve_sources_falls_back_to_the_browser(episode_page, monkeypatch):
    monkeypatch.setattr(
        toonworld,
        "resolve_interstitial",
        lambda token, **_kw: {
            "destination": "https://shrinkme.click/RPFhm3l",
            "shortener": "https://shrinkme.click/RPFhm3l",
            "host": "gdflix",
            "file_id": "placeholder",
            "props": {},
            "error": None,
        },
    )
    monkeypatch.setattr(
        toonworld_hosts,
        "resolve",
        lambda host, file_id, **_kw: toonworld_hosts.HostResult(
            state="missing", detail="the host says the file does not exist"
        ),
    )
    def _browser_links(quality, _url):
        return {
            source["token"]: {
                "links": [
                    {
                        "host": "gdflix",
                        "file_id": "realid123",
                        "url": "https://new10.gdflix.io/file/realid123",
                    }
                ],
                "error": None,
            }
            for source in quality.get("sources", [])
        }

    monkeypatch.setattr(toonworld, "_browser_links_for_sources", _browser_links)
    seen = {}

    def _resolve(host, file_id, **_kw):
        seen[host] = file_id
        if file_id == "realid123":
            return toonworld_hosts.HostResult(
                url="https://drive.google.com/file/d/realid123/view",
                state="ready",
                detail="resolved to a Google Drive link",
            )
        return toonworld_hosts.HostResult(
            state="missing", detail="the host says the file does not exist"
        )

    monkeypatch.setattr(toonworld_hosts, "resolve", _resolve)
    sources = toonworld.resolve_sources(
        "https://archive.toonworld4all.me/episode/naruto-shippuden-1x373", "480p-h264-8bit"
    )
    assert sources
    assert all(source["state"] == "ready" for source in sources)
    assert "realid123" in seen.values()


def test_verify_media_file_rejects_a_placeholder(tmp_path):
    placeholder = tmp_path / "episode.mp4"
    placeholder.write_bytes(b"video-bytes")
    problem = ytdlp_client.verify_media_file(placeholder)
    assert problem is not None
    assert "placeholder" in problem


def test_verify_media_file_rejects_a_tiny_file(tmp_path):
    tiny = tmp_path / "episode.mp4"
    tiny.write_bytes(b"x" * 100)
    assert ytdlp_client.verify_media_file(tiny) is not None


def test_verify_media_file_rejects_a_file_without_media(tmp_path):
    if not shutil.which("ffprobe"):
        pytest.skip("ffprobe is not installed")
    empty = tmp_path / "episode.mp4"
    empty.write_bytes(b"\x00" * (200 * 1024))
    problem = ytdlp_client.verify_media_file(empty)
    assert problem is not None
    assert "media" in problem


def test_verify_media_file_accepts_a_real_file(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is not installed")
    real = tmp_path / "episode.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=320x240:rate=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-shortest",
            "-y",
            str(real),
        ],
        check=True,
        capture_output=True,
    )
    assert ytdlp_client.verify_media_file(real) is None


def test_verify_media_file_rejects_a_file_below_the_announced_size(tmp_path):
    if not shutil.which("ffprobe"):
        pytest.skip("ffprobe is not installed")
    real = tmp_path / "episode.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=320x240:rate=10",
            "-y",
            str(real),
        ],
        check=True,
        capture_output=True,
    )
    problem = ytdlp_client.verify_media_file(real, expected_size=500 * 1024 * 1024)
    assert problem is not None
    assert "announced" in problem


def test_base_opts_builds_a_structured_impersonate_target():
    opts = ytdlp_client.base_opts(impersonate="chrome")
    target = opts.get("impersonate")
    assert target is not None
    assert isinstance(target, yt_dlp.networking.impersonate.ImpersonateTarget)
    assert str(target) == "chrome"
