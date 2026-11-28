"""Tests for the native toonworld4all.me support.

The fixtures under ``tests/fixtures`` are trimmed copies of real pages, so
the whole extraction pipeline is exercised without touching the network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.sources import http, toonworld  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"

EPISODE_URL = "https://archive.toonworld4all.me/episode/naruto-shippuden-1x373"
MOVIE_URL = "https://archive.toonworld4all.me/movie/steps-2026"
FRONT_MOVIE_URL = "https://toonworld4all.me/steps-2026-multi-audio-hindi/"
SHOW_URL = "https://toonworld4all.me/naruto-shippuden-season-18-episodes-bluray-hindi-multi-audio/"
CATEGORY_URL = "https://toonworld4all.me/category/netflix/"
LIST_URL = "https://toonworld4all.me/all-movies-list_25/"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8", errors="replace")


@pytest.fixture()
def fake_fetch(monkeypatch):
    """Serve the local fixtures instead of performing real requests."""

    def _fetch(url, **kwargs):
        if "/episode/" in url:
            return 200, read_fixture("episode_page.html"), {}
        if "/movie/" in url:
            return 200, read_fixture("episode_page.html"), {}
        if "category/" in url:
            return 200, read_fixture("category_page.html"), {}
        if "all-movies-list" in url:
            return 200, read_fixture("movies_list.html"), {}
        if "/redirect/" in url:
            return 200, read_fixture("redirect_page.html"), {}
        if "steps-2026" in url:
            return 200, read_fixture("movie_page.html"), {}
        if "toonworld4all.me" in url:
            return 200, read_fixture("season_page.html"), {}
        return 404, "", {}

    monkeypatch.setattr(http, "fetch", _fetch)
    return _fetch


# --------------------------------------------------------------------------- #
# routing
# --------------------------------------------------------------------------- #


def test_matches_only_toonworld_urls():
    assert toonworld.matches(SHOW_URL)
    assert toonworld.matches(EPISODE_URL)
    assert toonworld.matches(CATEGORY_URL)
    assert not toonworld.matches("https://www.youtube.com/watch?v=abc")


def test_archive_episode_is_a_video(fake_fetch):
    analysis = toonworld.probe(EPISODE_URL)
    assert analysis["kind"] == "video"
    assert len(analysis["qualities"]) == 6
    assert {q["selector"] for q in analysis["qualities"]} == {
        f"toonworld:{q['id']}" for q in analysis["qualities"]
    }


def test_archive_movie_is_a_video(fake_fetch):
    analysis = toonworld.probe(MOVIE_URL)
    assert analysis["kind"] == "video"
    assert analysis["qualities"]


def test_front_end_movie_page_resolves_to_the_archive_movie(fake_fetch):
    analysis = toonworld.probe(FRONT_MOVIE_URL)
    assert analysis["kind"] == "video"
    assert analysis["qualities"]
    # the page the user pasted is kept for the download
    assert analysis["webpage_url"] == FRONT_MOVIE_URL


def test_show_page_is_a_playlist_of_episodes(fake_fetch):
    analysis = toonworld.probe(SHOW_URL)
    assert analysis["kind"] == "playlist"
    assert analysis["playlist_count"] >= 18
    assert all(e["url"].startswith("https://archive.toonworld4all.me/episode/") for e in analysis["entries"])


def test_category_page_is_a_playlist_of_titles(fake_fetch):
    analysis = toonworld.probe(CATEGORY_URL)
    assert analysis["kind"] == "playlist"
    assert analysis["playlist_count"] == 3
    assert all(e["url"].startswith("https://toonworld4all.me/") for e in analysis["entries"])
    # the navigation menu is not a title
    assert not any("first-on-ne" in e["url"] for e in analysis["entries"])


def test_list_page_is_a_playlist_of_titles(fake_fetch):
    analysis = toonworld.probe(LIST_URL)
    assert analysis["kind"] == "playlist"
    assert analysis["playlist_count"] == 4
    assert all(e["title"] for e in analysis["entries"])


def test_archive_item_url_finds_the_movie(fake_fetch):
    assert toonworld.archive_item_url(FRONT_MOVIE_URL) == MOVIE_URL
    assert toonworld.archive_item_url(SHOW_URL) == EPISODE_URL


def test_listing_entries_keep_their_titles(fake_fetch):
    analysis = toonworld.probe(CATEGORY_URL)
    titles = [e["title"] for e in analysis["entries"]]
    assert titles and all(len(title) > 3 for title in titles)


def test_probe_season_is_still_a_season_page(fake_fetch):
    analysis = toonworld.probe_season(SHOW_URL)
    assert analysis["kind"] == "playlist"
    assert analysis["playlist_count"] >= 18
