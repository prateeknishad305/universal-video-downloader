"""Tests for the native MultiMovies source."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.sources import embed_resolver, multimovies

FIXTURES = Path(__file__).parent / "fixtures"
EPISODE_URL = "https://m1.multimovies.courses/series/whats-new-scooby-doo-57052/season/3/episode/14"
SERIES_URL = "https://m1.multimovies.courses/series/whats-new-scooby-doo-57052"

MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080
https://cdn.example.com/hls/1080/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1280x720
https://cdn.example.com/hls/720/index.m3u8
"""


class _FakeHttp:
    """Serve the local fixtures instead of the real site."""

    def __init__(self, pages: dict, manifests: dict):
        self.pages = pages
        self.manifests = manifests
        self.calls: list = []

    def __call__(self, url, **kwargs):
        self.calls.append(url)
        if url in self.pages:
            return 200, self.pages[url], {"content-type": "text/html"}
        if url in self.manifests:
            return 200, self.manifests[url], {"content-type": "application/vnd.apple.mpegurl"}
        if "cdn.example.com" in url:
            return 200, self.manifests["*"], {"content-type": "application/vnd.apple.mpegurl"}
        return 404, "not found", {}

    def fetch(self, url, **kwargs):
        return self(url, **kwargs)


@pytest.fixture
def episode_page():
    return (FIXTURES / "mm_episode.html").read_text(encoding="utf-8")


@pytest.fixture
def series_page():
    return (FIXTURES / "mm_series.html").read_text(encoding="utf-8")


def test_matches_known_hosts():
    assert multimovies.matches(EPISODE_URL)
    assert multimovies.matches("https://multimovies.wtf/")
    assert multimovies.matches("https://m1.multimovies.courses/movie/dune-part-two")
    assert not multimovies.matches("https://toonworld4all.me/season/18")
    assert not multimovies.matches("https://youtube.com/watch?v=abc")


def test_extract_json_object_reads_nested_watch_config(episode_page):
    config = embed_resolver.extract_json_object(episode_page, "watchConfig")
    assert config is not None
    assert isinstance(config["initialServers"], list)
    assert len(config["initialServers"]) == 6
    names = {server["name"] for server in config["initialServers"]}
    assert "Vidbolt" in names and "Vidrift" in names


def test_probe_series_builds_playlist(monkeypatch, series_page):
    fake = _FakeHttp({SERIES_URL: series_page}, {})
    monkeypatch.setattr(multimovies.http, "fetch", fake.fetch)
    result = multimovies.probe(SERIES_URL)
    assert result["kind"] == "playlist"
    assert result["extractor"] == "MultiMovies"
    assert result["playlist_count"] >= 14
    assert all(entry["url"].startswith("https://m1.multimovies.courses/series/") for entry in result["entries"])
    assert any("episode" in entry["title"].lower() for entry in result["entries"])


def test_probe_episode_reports_servers_and_qualities(monkeypatch, episode_page):
    fake = _FakeHttp({EPISODE_URL: episode_page}, {"*": MASTER})
    monkeypatch.setattr(multimovies.http, "fetch", fake.fetch)
    monkeypatch.setattr(
        multimovies,
        "resolve_embed",
        lambda url, allow_browser=False: {
            "method": "test",
            "media": ["https://cdn.example.com/hls/master.m3u8"],
            "error": None,
        },
    )

    result = multimovies.probe(EPISODE_URL)
    assert result["kind"] == "video"
    assert result["title"] == "E-Scream"
    assert {q["id"] for q in result["qualities"]} == {"1080p", "720p"}
    assert result["qualities"][0]["id"] == "1080p"
    assert result["qualities"][0]["selector"] == "multimovies:1080p"
    # every server contributes one format per quality it offers
    assert len(result["formats"]) == 12
    assert all(f["protocol"] == "m3u8" for f in result["formats"])
    assert all(f["media_url"].endswith("index.m3u8") for f in result["formats"])
    assert len(result["sources_by_quality"]["1080p"]) == 6
    assert {f["server"] for f in result["formats"]} == {
        server["name"] for server in result["servers"]
    }


def test_probe_episode_keeps_unresolved_servers_visible(monkeypatch, episode_page):
    fake = _FakeHttp({EPISODE_URL: episode_page}, {})
    monkeypatch.setattr(multimovies.http, "fetch", fake.fetch)
    monkeypatch.setattr(
        multimovies,
        "resolve_embed",
        lambda url, allow_browser=False: {"method": "test", "media": [], "error": "no resolver matched"},
    )
    with pytest.raises(multimovies.MultiMoviesError):
        multimovies.probe(EPISODE_URL)


def test_resolve_sources_fails_over_between_servers(monkeypatch, episode_page):
    fake = _FakeHttp({EPISODE_URL: episode_page}, {"*": MASTER})
    monkeypatch.setattr(multimovies.http, "fetch", fake.fetch)

    calls: list = []
    seen: dict = {}

    def fake_resolve(url, allow_browser=False):
        calls.append(url)
        seen[url] = seen.get(url, 0) + 1
        # the first pass (analysis) succeeds, the second pass (download) fails
        if "vidrift" in url and seen[url] > 1:
            return {"method": "vidrift", "media": [], "error": "expired token"}
        return {"method": "vidbolt", "media": ["https://cdn.example.com/hls/master.m3u8"], "error": None}

    monkeypatch.setattr(multimovies, "resolve_embed", fake_resolve)

    sources = multimovies.resolve_sources(EPISODE_URL, "1080p")
    states = [source["state"] for source in sources]
    assert "unresolved" in states
    assert "ready" in states
    ready = [s for s in sources if s["state"] == "ready"]
    assert ready[0]["url"].endswith("1080/index.m3u8")


def test_resolve_manifest_parses_variants(monkeypatch):
    fake = _FakeHttp({}, {"*": MASTER})
    monkeypatch.setattr(embed_resolver.http, "fetch", fake.fetch)
    variants = embed_resolver.resolve_manifest("https://cdn.example.com/hls/master.m3u8")
    heights = [variant["height"] for variant in variants]
    assert heights == [1080, 720]
    assert variants[0]["bandwidth"] == 5000000
    assert variants[0]["url"].endswith("1080/index.m3u8")
