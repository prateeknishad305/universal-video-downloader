"""Offline tests for the toonworld4all.me source.

The fixtures under ``tests/fixtures`` are trimmed copies of real pages, so the
whole extraction pipeline is exercised without touching the network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.sources import http, toonworld  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


@pytest.fixture()
def fake_fetch(monkeypatch):
    """Serve the local fixtures instead of performing real requests."""

    def _fetch(url, **kwargs):
        if "/episode/" in url:
            return 200, read_fixture("episode_page.html"), {}
        if "/redirect/" in url:
            return 200, read_fixture("redirect_page.html"), {}
        if "toonworld4all.me" in url:
            return 200, read_fixture("season_page.html"), {}
        return 404, "", {}

    monkeypatch.setattr(http, "fetch", _fetch)
    return _fetch


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #


def test_matches_only_toonworld_urls():
    assert toonworld.matches("https://toonworld4all.me/naruto-shippuden-season-18/")
    assert toonworld.matches("https://archive.toonworld4all.me/episode/naruto-shippuden-1x373")
    assert not toonworld.matches("https://www.youtube.com/watch?v=abc")


def test_probe_season_returns_episode_playlist(fake_fetch):
    analysis = toonworld.probe(
        "https://toonworld4all.me/naruto-shippuden-season-18-episodes-bluray-hindi-multi-audio/"
    )
    assert analysis["kind"] == "playlist"
    assert analysis["playlist_count"] == 18
    first = analysis["entries"][0]
    assert first["url"].endswith("/episode/naruto-shippuden-1x373")
    assert "373" in first["title"]


def test_probe_episode_returns_quality_ladder(fake_fetch):
    analysis = toonworld.probe("https://archive.toonworld4all.me/episode/naruto-shippuden-1x373")
    assert analysis["kind"] == "video"
    assert analysis["extractor"] == "ToonWorld4All"
    assert "Team 7 Assemble" in analysis["title"]
    assert len(analysis["formats"]) == 24  # 6 qualities x 4 hosts
    assert len(analysis["qualities"]) == 6

    ids = [q["id"] for q in analysis["qualities"]]
    assert "480p-h264-8bit" in ids
    assert "1080p-h265-hq-10bit" in ids
    for quality in analysis["qualities"]:
        assert quality["selector"] == f"toonworld:{quality['id']}"
        assert len(quality["sources"]) == 4


def test_quality_slugs_are_unique():
    encodes = [
        {"resolution": "1080p", "codec": "H.264", "bit_depth": 8, "is_hq": False},
        {"resolution": "1080p", "codec": "H.265", "bit_depth": 10, "is_hq": False},
        {"resolution": "1080p", "codec": "H.265", "bit_depth": 10, "is_hq": True},
    ]
    slugs = [toonworld._quality_slug(encode) for encode in encodes]
    assert len(set(slugs)) == 3


def test_resolve_destination_reads_the_blurred_code(fake_fetch):
    destination, error = toonworld.resolve_destination("abc123")
    assert error is None
    # The identifier is hidden behind a CSS blur but still present in the HTML.
    assert destination == "https://hubcloud.mov/drive/auwtyzio2i1jqat"


def test_resolve_sources_returns_one_entry_per_host(fake_fetch):
    sources = toonworld.resolve_sources(
        "https://archive.toonworld4all.me/episode/naruto-shippuden-1x373", "480p-h264-8bit"
    )
    assert len(sources) == 4
    assert {s["host"] for s in sources} == {"HubCloud", "Filepress", "GDFlix", "MEGA"}


def test_validate_host_id_flags_the_wrong_id_format():
    assert toonworld.validate_host_id("Filepress", "1qgzbq5ve6yt3x5jn56zrzv0")
    assert toonworld.validate_host_id("Filepress", "0123456789abcdef01234567") is None
    assert toonworld.validate_host_id("MEGA", "x" * 52)
    assert toonworld.validate_host_id("MEGA", "y" * 22) is None
    assert toonworld.validate_host_id("HubCloud", "anything-goes") is None


def test_probe_destination_classifies_a_missing_file(monkeypatch):
    monkeypatch.setattr(
        http, "fetch", lambda url, **kw: (404, "404 ! File Not Found", {"content-type": "text/html"})
    )
    result = toonworld.probe_destination("https://hubcloud.mov/drive/abc")
    assert result["state"] == "missing"


def test_probe_destination_classifies_a_cloudflare_block(monkeypatch):
    monkeypatch.setattr(
        http, "fetch",
        lambda url, **kw: (403, "<title>Just a moment...</title>", {"content-type": "text/html"}),
    )
    result = toonworld.probe_destination("https://new4.filepress.lat/file/abc")
    assert result["state"] == "blocked"
    assert "Cloudflare" in result["detail"]


def test_probe_destination_detects_a_real_file(monkeypatch):
    monkeypatch.setattr(
        http, "fetch",
        lambda url, **kw: (200, "binary", {"content-type": "video/mp4"}),
    )
    result = toonworld.probe_destination("https://example.com/video.mp4")
    assert result["state"] == "ready"


def test_probe_destination_detects_a_removed_file(monkeypatch):
    monkeypatch.setattr(
        http, "fetch",
        lambda url, **kw: (
            200,
            "<html>The file you are trying to download is no longer available!</html>",
            {"content-type": "text/html"},
        ),
    )
    result = toonworld.probe_destination("https://gdflix.dev/file/abc")
    assert result["state"] == "missing"


def test_direct_file_links_are_used_without_decoding(monkeypatch):
    """A site may store a plain file URL; it must be honoured as-is."""
    props = (
        '<!DOCTYPE html><html><body><script>window.__PROPS__ = '
        '{"data":{"error":false,"data":{"metadata":{"slug":"x","season":1,"episode":1,'
        '"name":"Ep","show":"Show","runtime":24},'
        '"encodes":[{"resolution":"720p","codec":"H.264","bit_depth":8,"size":100,'
        '"readable":{"codec":"720p x264","size":"1 MB"},'
        '"files":[{"host":"Self Hosted","link":"https://cdn.example.com/movie.mp4",'
        '"short":"cdn.example.com"}]}]}}};</script></body></html>'
    )

    def _fetch(url, **kwargs):
        return 200, props, {}

    monkeypatch.setattr(http, "fetch", _fetch)
    analysis = toonworld.probe_episode("https://archive.toonworld4all.me/episode/x-1x1")
    source = analysis["qualities"][0]["sources"][0]
    assert source["direct"] == "https://cdn.example.com/movie.mp4"
    assert source["token"] == ""


def test_resolve_appdrive_follows_the_javascript_redirect(monkeypatch):
    pages = [
        (
            200,
            "<html><body><script>window.location.replace("
            "'https://appdrive.life/api/download/abc?ch=1&js=TOKEN')</script></body></html>",
            {},
        ),
        (200, '<html><script>var finalUrl = "https://cdn.appdrive.life/movie.mp4";</script></html>', {}),
    ]
    calls = []

    def _fetch(url, **kwargs):
        calls.append(url)
        return pages[min(len(calls) - 1, len(pages) - 1)]

    monkeypatch.setattr(http, "fetch", _fetch)
    destination, error = toonworld.resolve_appdrive("abc")
    assert error is None
    assert destination == "https://cdn.appdrive.life/movie.mp4"
    assert len(calls) == 2


def test_probe_destination_handles_a_404_body_with_http_200(monkeypatch):
    monkeypatch.setattr(
        http, "fetch",
        lambda url, **kw: (200, "404 ! File Not Found", {"content-type": "text/html"}),
    )
    result = toonworld.probe_destination("https://hubcloud.mov/drive/abc")
    assert result["state"] == "missing"
