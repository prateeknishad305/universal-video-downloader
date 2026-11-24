"""Tests for the Telegram bot helpers (no network, no Telegram token needed)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import telegram_bot  # noqa: E402
from app.telegram_bot import (  # noqa: E402
    build_episode_buttons,
    build_quality_buttons,
    can_upload,
    describe_analysis,
    extract_url,
    format_caption,
    format_progress,
    format_size,
)

VIDEO = {
    "kind": "video",
    "title": "Naruto: Shippuden - S1E373 - Team 7 Assemble!",
    "uploader": "Drago",
    "duration": 1380,
    "extractor": "ToonWorld4All",
    "qualities": [
        {"id": "480p-h264-8bit", "label": "480p x264 - 113.23 MB", "size": 118727523},
        {"id": "1080p-h265-hq-10bit", "label": "1080p HEVC 10bit [HQ] - 1.09 GB", "size": 1169000000},
    ],
}

PLAYLIST = {
    "kind": "playlist",
    "title": "Naruto Shippuden Season 18",
    "playlist_count": 2,
    "entries": [
        {"id": "a", "title": "Naruto Shippuden 1X373", "url": "https://x/episode/a"},
        {"id": "b", "title": "Naruto Shippuden 1X374", "url": "https://x/episode/b"},
    ],
}


def test_format_size():
    assert format_size(0) == "unknown size"
    assert format_size(None) == "unknown size"
    assert format_size(512) == "512.0 B"
    assert format_size(1536) == "1.5 KB"
    assert format_size(118727523) == "113.2 MB"


def test_extract_url():
    assert extract_url("https://youtu.be/abc") == "https://youtu.be/abc"
    assert extract_url("watch this https://example.com/video?id=1 please") == (
        "https://example.com/video?id=1"
    )
    assert extract_url("no link here") is None


def test_describe_analysis_video():
    text = describe_analysis(VIDEO)
    assert "Team 7 Assemble" in text
    assert "23:00" in text
    assert "ToonWorld4All" in text


def test_describe_analysis_playlist():
    text = describe_analysis(PLAYLIST)
    assert "Playlist with 2 items." in text


def test_build_quality_buttons():
    rows = build_quality_buttons(VIDEO, "plan123")
    assert len(rows) == 2
    assert rows[0][0]["data"] == "dl|480p-h264-8bit|plan123"
    assert "113.2 MB" in rows[0][0]["text"]
    assert rows[1][0]["data"].startswith("dl|1080p-h265-hq-10bit|")


def test_build_quality_buttons_respects_the_callback_budget():
    rows = build_quality_buttons(VIDEO, "x" * 40)
    assert all(len(row[0]["data"].encode()) <= 64 for row in rows)
    assert all(len(row[0]["text"]) <= 60 for row in rows)


def test_build_episode_buttons():
    rows = build_episode_buttons(PLAYLIST, "plan123")
    assert len(rows) == 2
    assert rows[0][0]["data"] == "ep|0|plan123"
    assert rows[1][0]["text"].startswith("2.")


def test_format_progress():
    job = {
        "status": "downloading",
        "progress": 42.5,
        "downloaded_bytes": 50 * 1024 * 1024,
        "total_bytes": 100 * 1024 * 1024,
        "speed": 1024 * 1024,
        "eta": 30,
        "stage": "Downloading",
    }
    line = format_progress(job)
    assert "42.5%" in line
    assert "50.0 MB / 100.0 MB" in line
    assert "ETA 30s" in line


def test_format_progress_terminal_states():
    assert "Done" in format_progress({"status": "completed", "filename": "a.mp4"})
    assert "Failed" in format_progress({"status": "error", "error": "boom"})
    assert "Cancelled" in format_progress({"status": "cancelled"})


def test_can_upload():
    assert can_upload(10 * 1024 * 1024)
    assert not can_upload(500 * 1024 * 1024)
    assert can_upload(None)


def test_format_caption():
    caption = format_caption(
        {"title": "Episode 1", "quality_label": "1080p", "filename": "ep1.mp4"}
    )
    assert "Episode 1" in caption
    assert "1080p" in caption
    assert "ep1.mp4" in caption


def test_bot_does_not_start_without_a_token():
    from app import config

    original = config.TELEGRAM_BOT_TOKEN
    config.TELEGRAM_BOT_TOKEN = None
    try:
        assert telegram_bot.build_application(object()) is None
    finally:
        config.TELEGRAM_BOT_TOKEN = original


def test_build_application_with_a_token():
    """With a token configured the application must be constructed."""
    from app import config

    original = config.TELEGRAM_BOT_TOKEN
    config.TELEGRAM_BOT_TOKEN = "123456789:AAHfakeTokenForTestingPurposesOnly"
    try:
        application = telegram_bot.build_application(object())
        assert application is not None
        assert application.bot is not None
    finally:
        config.TELEGRAM_BOT_TOKEN = original
