"""Offline tests for the toonworld4all.me source.

The fixtures under ``tests/fixtures`` are trimmed copies of real pages, so the
whole extraction pipeline is exercised without touching the network.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.sources import http, toonworld, toonworld_hosts  # noqa: E402

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


@pytest.fixture(autouse=True)
def no_browser(monkeypatch):
    """Keep the browser fallback out of the offline tests."""
    monkeypatch.setattr(toonworld, "_browser_links_for_sources", lambda *a, **kw: {})
    return None


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


def test_probe_season_uses_the_heading_title_and_the_episode_link(fake_fetch):
    analysis = toonworld.probe(
        "https://toonworld4all.me/naruto-shippuden-season-18-episodes-bluray-hindi-multi-audio/"
    )
    titles = [entry["title"] for entry in analysis["entries"]]
    assert "Episode 373: Team 7, Assemble!" in titles
    first = analysis["entries"][0]
    assert first["id"] == "naruto-shippuden-1x373"
    assert first["url"] == "https://archive.toonworld4all.me/episode/naruto-shippuden-1x373"
    assert all("Episode" in title for title in titles)


def test_probe_season_keeps_the_headings_apart(monkeypatch):
    body = (
        '<div class="mks_accordion_heading"><strong>Episode 1: One</strong></div>'
        '<div class="mks_accordion_content">'
        '<a href="https://archive.toonworld4all.me/episode/show-1x1">Watch</a></div>'
        '<div class="mks_accordion_heading"><strong>Episode 2: Two</strong></div>'
        '<div class="mks_accordion_content">'
        '<a href="https://archive.toonworld4all.me/episode/show-1x2">Watch</a></div>'
    )
    monkeypatch.setattr(http, "fetch", lambda url, **kw: (200, body, {}))
    analysis = toonworld.probe_season("https://toonworld4all.me/show-season-1/")
    assert [entry["id"] for entry in analysis["entries"]] == ["show-1x1", "show-1x2"]
    assert [entry["title"] for entry in analysis["entries"]] == [
        "Episode 1: One",
        "Episode 2: Two",
    ]


def test_probe_season_accepts_relative_episode_links(monkeypatch):
    body = (
        '<div class="mks_accordion_heading"><strong>Episode 5: Five</strong></div>'
        '<div class="mks_accordion_content">'
        '<a href="/episode/show-1x5">Watch</a></div>'
    )
    monkeypatch.setattr(http, "fetch", lambda url, **kw: (200, body, {}))
    analysis = toonworld.probe_season("https://toonworld4all.me/show-season-1/")
    assert analysis["entries"][0]["url"] == (
        "https://archive.toonworld4all.me/episode/show-1x5"
    )


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


def test_resolve_sources_refetches_the_page_for_a_fresh_token(monkeypatch):
    """The site mints a new redirect token on every page load.

    A token captured during the analysis is worthless minutes later, so the
    download path must resolve against a freshly fetched episode page.
    """
    page = read_fixture("episode_page.html")
    stale = re.sub(r"/redirect/[a-f0-9]+", "/redirect/" + "a" * 64, page)
    fresh = re.sub(r"/redirect/[a-f0-9]+", "/redirect/" + "b" * 64, page)
    served = {"n": 0}

    def _fetch(url, **kwargs):
        if "/episode/" in url:
            served["n"] += 1
            return 200, stale if served["n"] == 1 else fresh, {}
        if "/redirect/" in url:
            return 200, read_fixture("redirect_page.html"), {}
        return 404, "", {}

    monkeypatch.setattr(http, "fetch", _fetch)
    monkeypatch.setattr(
        toonworld,
        "probe_destination",
        lambda url: {"state": "ok", "detail": ""},
    )
    monkeypatch.setattr(
        toonworld,
        "resolve_destination",
        lambda token: (f"https://files.example.com/{token}", None),
    )

    # 1. the analysis, which captures a token
    toonworld.probe_episode("https://archive.toonworld4all.me/episode/naruto-shippuden-1x373")
    assert served["n"] == 1

    # 2. the download, which must not reuse the captured token
    sources = toonworld.resolve_sources(
        "https://archive.toonworld4all.me/episode/naruto-shippuden-1x373",
        "480p-h264-8bit",
    )
    assert served["n"] == 2, "the episode page must be fetched again for the download"
    tokens = {source["token"] for source in sources}
    assert tokens, "no sources were resolved"
    assert all(token == "b" * 64 for token in tokens), tokens
    assert not any(token == "a" * 64 for token in tokens)


def test_validate_host_id_flags_the_wrong_id_format():
    assert toonworld.validate_host_id("Filepress", "1qgzbq5ve6yt3x5jn56zrzv0")
    assert toonworld.validate_host_id("Filepress", "0123456789abcdef01234567") is None
    assert toonworld.validate_host_id("MEGA", "x" * 52)
    assert toonworld.validate_host_id("MEGA", f"{'y' * 22}#{'k' * 43}") is None
    assert toonworld.validate_host_id("HubCloud", "anything-goes") is None


def test_validate_host_id_names_the_mega_handle_and_not_the_key():
    handle = "y" * 22
    key = "z" * 43
    problem = toonworld.validate_host_id("MEGA", f"{handle}#{key}")
    assert problem is None
    broken = toonworld.validate_host_id("MEGA", f"{'x' * 8}#{key}")
    assert "8 chars" in broken
    assert "22 characters" in broken


def test_host_id_from_url_keeps_the_whole_mega_handle():
    handle = "abcdefghijklmnopqrstuv"
    key = "k" * 43
    host, file_id = toonworld.host_id_from_url(f"https://mega.nz/file/{handle}#{key}")
    assert host == "mega"
    assert file_id == f"{handle}#{key}"


def _single_source_episode(monkeypatch, host="HubCloud", token="t" * 64):
    monkeypatch.setattr(
        toonworld,
        "probe_episode",
        lambda url: {
            "qualities": [
                {
                    "id": "480p",
                    "size": 1234,
                    "sources": [{"host": host, "token": token}],
                }
            ]
        },
    )
    monkeypatch.setattr(
        toonworld, "probe_destination", lambda url: {"state": "ready", "detail": ""}
    )


def test_browser_fallback_is_used_when_the_page_hides_the_file_id(monkeypatch):
    """A redirect page without a file id must not end the resolution."""
    _single_source_episode(monkeypatch)
    monkeypatch.setattr(
        toonworld,
        "resolve_interstitial",
        lambda token, timeout=60: {
            "host": "https://hubcloud.mov/drive/",
            "file_id": None,
            "error": None,
        },
    )
    monkeypatch.setattr(
        toonworld,
        "_browser_links_for_sources",
        lambda quality, url, timeout=150: {
            "t" * 64: {
                "links": [
                    {
                        "host": "hubcloud",
                        "file_id": "cw5hwamg0sy0by7",
                        "url": "https://hubcloud.mov/drive/cw5hwamg0sy0by7",
                    }
                ],
                "error": None,
            }
        },
    )
    seen = {}

    def _resolve(host, file_id, **kwargs):
        seen["host"] = host
        seen["file_id"] = file_id
        seen["domain"] = kwargs.get("domain")
        return toonworld_hosts.HostResult(
            url="https://hubcloud.mov/drive/cw5hwamg0sy0by7",
            state="ready",
            detail="resolved",
        )

    monkeypatch.setattr(toonworld_hosts, "resolve", _resolve)
    sources = toonworld.resolve_sources("https://archive.toonworld4all.me/episode/x", "480p")
    assert sources[0]["url"] == "https://hubcloud.mov/drive/cw5hwamg0sy0by7"
    assert sources[0]["state"] == "ready"
    assert seen["file_id"] == "cw5hwamg0sy0by7"
    assert seen["domain"] == "hubcloud.mov"


def test_browser_fallback_is_used_when_the_stored_id_is_dead(monkeypatch):
    """A placeholder id that 404s must be replaced by the browser result."""
    _single_source_episode(monkeypatch)
    monkeypatch.setattr(
        toonworld,
        "resolve_interstitial",
        lambda token, timeout=60: {
            "host": "https://hubcloud.mov/drive/",
            "file_id": "cw5hwamg0sy0by7",
            "error": None,
        },
    )
    monkeypatch.setattr(
        toonworld,
        "_browser_links_for_sources",
        lambda quality, url, timeout=150: {
            "t" * 64: {
                "links": [
                    {
                        "host": "hubcloud",
                        "file_id": "freshid123",
                        "url": "https://hubcloud.mov/drive/freshid123",
                    }
                ],
                "error": None,
            }
        },
    )

    def _resolve(host, file_id, **kwargs):
        if file_id == "freshid123":
            return toonworld_hosts.HostResult(
                url="https://hubcloud.mov/drive/freshid123", state="ready", detail="resolved"
            )
        return toonworld_hosts.HostResult(
            state="missing", detail="HubCloud says the file does not exist"
        )

    monkeypatch.setattr(toonworld_hosts, "resolve", _resolve)
    sources = toonworld.resolve_sources("https://archive.toonworld4all.me/episode/x", "480p")
    assert sources[0]["url"] == "https://hubcloud.mov/drive/freshid123"
    assert sources[0]["state"] == "ready"


def test_browser_fallback_reason_is_reported_when_it_cannot_run(monkeypatch):
    _single_source_episode(monkeypatch)
    monkeypatch.setattr(
        toonworld,
        "resolve_interstitial",
        lambda token, timeout=60: {
            "host": "https://hubcloud.mov/drive/",
            "file_id": None,
            "error": None,
        },
    )
    monkeypatch.setattr(toonworld, "_browser_links_for_sources", lambda *a, **kw: {})
    monkeypatch.setattr(
        toonworld,
        "_browser_fallback_reason",
        lambda: "the browser fallback is not available on this deployment",
    )
    sources = toonworld.resolve_sources("https://archive.toonworld4all.me/episode/x", "480p")
    assert sources[0]["url"] is None
    assert "browser fallback is not available" in sources[0]["detail"]


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
