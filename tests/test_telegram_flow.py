"""End-to-end test of the Telegram bot control flow with mocked Telegram objects.

The bot talks to Telegram through a thin surface (message.reply_text,
message.edit_text, message.reply_document, query.edit_message_text), so the
whole flow can be driven without a token or a network connection.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import telegram_bot  # noqa: E402
from app.jobs import JobManager  # noqa: E402
from app.storage import MemoryJobStore  # noqa: E402


class FakeMessage:
    def __init__(self, text: str = ""):
        self.text = text
        self.replies: list[str] = []
        self.edits: list[str] = []
        self.documents: list[str] = []
        self.videos: list[str] = []
        self.markup = None

    async def reply_text(self, text, reply_markup=None, **kwargs):
        self.replies.append(text)
        self.markup = reply_markup
        return self

    async def edit_text(self, text, reply_markup=None, **kwargs):
        self.edits.append(text)
        self.markup = reply_markup
        return self

    async def reply_document(self, document=None, caption=None, **kwargs):
        self.documents.append(str(document))
        return self

    async def reply_video(self, video=None, caption=None, **kwargs):
        self.videos.append(str(video))
        return self


class FakeChat:
    def __init__(self, chat_id: int = 42):
        self.id = chat_id


class FakeUpdate:
    def __init__(self, text: str = "", chat_id: int = 42):
        self.effective_message = FakeMessage(text)
        self.effective_chat = FakeChat(chat_id)
        self.callback_query = None


class FakeQuery:
    def __init__(self, data: str):
        self.data = data
        self.answers: list[str] = []
        self.edits: list[str] = []
        self.markup = None
        self.message = None

    async def answer(self, text=None, show_alert=False):
        self.answers.append(text or "")

    async def edit_message_text(self, text, reply_markup=None, **kwargs):
        self.edits.append(text)
        self.markup = reply_markup
        # Telegram returns the edited message; the bot then keeps updating it.
        self.message = FakeMessage(text)
        return self.message


ANALYSIS = {
    "kind": "video",
    "title": "Naruto: Shippuden - S1E373 - Team 7 Assemble!",
    "uploader": "Drago",
    "duration": 1380,
    "extractor": "ToonWorld4All",
    "webpage_url": "https://archive.toonworld4all.me/episode/naruto-shippuden-1x373",
    "qualities": [
        {"id": "480p-h264-8bit", "label": "480p x264 - 113.23 MB", "size": 118727523,
         "selector": "toonworld:480p-h264-8bit", "kind": "merged"},
        {"id": "1080p-h265-hq-10bit", "label": "1080p HEVC 10bit [HQ] - 1.09 GB",
         "size": 1169000000, "selector": "toonworld:1080p-h265-hq-10bit", "kind": "merged"},
    ],
}


@pytest.fixture()
def bot(monkeypatch, tmp_path):
    manager = JobManager(store=MemoryJobStore())

    def fake_analyse(url):
        analysis = dict(ANALYSIS)
        analysis["webpage_url"] = url
        return analysis

    monkeypatch.setattr(manager, "analyse", fake_analyse)
    monkeypatch.setattr("app.telegram_bot.config.TELEGRAM_ALLOWED_CHATS", set())
    return telegram_bot.TelegramDownloadBot(manager), manager


def test_link_produces_a_quality_keyboard(bot):
    telegram, _ = bot
    update = FakeUpdate("please get https://archive.toonworld4all.me/episode/naruto-shippuden-1x373")

    asyncio.run(telegram.on_message(update, None))

    status = update.effective_message
    assert status.edits, "the bot should reply with an analysis"
    assert "Team 7 Assemble" in status.edits[-1]
    assert "Pick a quality" in status.edits[-1]
    assert status.markup is not None

    rows = status.markup.inline_keyboard
    assert len(rows) == 2
    assert rows[0][0].callback_data.startswith("dl|480p-h264-8bit|")


def test_quality_button_starts_a_download_and_uploads_the_file(bot, monkeypatch, tmp_path):
    telegram, manager = bot

    def fake_download(url, *, outtmpl, selector, mode="video", subtitles=False,
                      on_progress=None, on_log=None, should_cancel=None):
        target = Path(outtmpl.replace(".%(ext)s", ".mp4"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"video-bytes")
        if on_progress:
            on_progress({"downloaded_bytes": 10, "total_bytes": 10, "speed": 10.0, "eta": 0})
        return target

    monkeypatch.setattr("app.jobs.download_media", fake_download)

    update = FakeUpdate("https://archive.toonworld4all.me/episode/naruto-shippuden-1x373")
    asyncio.run(telegram.on_message(update, None))
    callback_data = update.effective_message.markup.inline_keyboard[0][0].callback_data

    query = FakeQuery(callback_data)
    update.callback_query = query

    async def run():
        await telegram.on_quality(update, None)

    asyncio.run(run())

    assert query.answers, "the callback query must be answered"
    assert any("Starting download" in edit for edit in query.edits)
    # progress is written into the message that the callback returned
    assert any("Done" in edit for edit in query.message.edits)
    assert query.message.edits[-1] == "Sent."
    assert len(query.message.videos) == 1
    assert query.message.videos[0].endswith(".mp4")


def test_unknown_chat_is_rejected_when_a_whitelist_is_set(bot, monkeypatch):
    telegram, _ = bot
    monkeypatch.setattr("app.telegram_bot.config.TELEGRAM_ALLOWED_CHATS", {"999"})
    update = FakeUpdate("https://example.com/video", chat_id=42)

    asyncio.run(telegram.on_message(update, None))

    assert not update.effective_message.edits
    assert not update.effective_message.replies


def test_history_command_lists_recorded_jobs(bot):
    telegram, manager = bot
    manager.create("https://example.com/video", title="Earlier download")

    update = FakeUpdate()
    asyncio.run(telegram.on_history(update, None))

    assert update.effective_message.replies
    assert "Earlier download" in update.effective_message.replies[0]
