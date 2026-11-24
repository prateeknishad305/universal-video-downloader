"""Telegram bot front end.

The bot is a thin skin over the same engine the web UI uses: it analyses a
link, offers the available qualities as an inline keyboard, downloads the
chosen one and uploads the finished file back into the chat.

Enable it by setting ``TELEGRAM_BOT_TOKEN`` (and optionally
``TELEGRAM_ALLOWED_CHATS`` to restrict who may use it).  When the token is
absent the bot never starts and the web app behaves exactly as before.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import config
from .jobs import JobManager

log = logging.getLogger(__name__)

try:  # pragma: no cover - depends on the environment
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

    TELEGRAM_AVAILABLE = True
except Exception:  # pragma: no cover
    TELEGRAM_AVAILABLE = False

    Application = Any  # type: ignore[assignment,misc]
    CallbackQueryHandler = CommandHandler = Any  # type: ignore[assignment,misc]


TELEGRAM_UPLOAD_LIMIT_MB = 50  # Bot API limit for sendDocument / sendVideo
_URL_RE = re.compile(r"https?://[^\s<>\"']+")


# --------------------------------------------------------------------------- #
# pure helpers (unit tested)
# --------------------------------------------------------------------------- #


def format_size(value: Optional[float]) -> str:
    """Human readable byte size."""
    if not value or value <= 0:
        return "unknown size"
    units = ("B", "KB", "MB", "GB", "TB")
    number = float(value)
    index = 0
    while number >= 1024 and index < len(units) - 1:
        number /= 1024
        index += 1
    return f"{number:.1f} {units[index]}"


def extract_url(text: str) -> Optional[str]:
    """Pull the first http(s) URL out of a message."""
    match = _URL_RE.search(text or "")
    if not match:
        return None
    return match.group(0).rstrip(".,);")


def describe_analysis(analysis: Dict[str, Any]) -> str:
    """Short summary of an analysed media item."""
    title = analysis.get("title") or "Untitled"
    lines = [f"*{title}*"]

    if analysis.get("kind") == "playlist":
        lines.append(f"Playlist with {analysis.get('playlist_count', 0)} items.")
        return "\n".join(lines)

    details = []
    if analysis.get("duration"):
        minutes = int(analysis["duration"] // 60)
        seconds = int(analysis["duration"] % 60)
        details.append(f"{minutes}:{seconds:02d}")
    if analysis.get("uploader"):
        details.append(str(analysis["uploader"]))
    if analysis.get("extractor"):
        details.append(str(analysis["extractor"]))
    if details:
        lines.append(" | ".join(details))
    return "\n".join(lines)


def build_quality_buttons(analysis: Dict[str, Any], plan_id: str) -> List[List[Dict[str, str]]]:
    """Inline-keyboard rows for the available qualities.

    Each button is ``{"text": ..., "data": ...}`` so the layout can be tested
    without the Telegram library installed.
    """
    rows: List[List[Dict[str, str]]] = []
    for quality in analysis.get("qualities", []):
        label = quality.get("label") or quality.get("id")
        size = quality.get("size")
        text = f"{label} ({format_size(size)})" if size else str(label)
        rows.append(
            [
                {
                    "text": text[:60],
                    "data": f"dl|{quality.get('id')}|{plan_id}"[:64],
                }
            ]
        )
    return rows


def build_episode_buttons(
    analysis: Dict[str, Any], plan_id: str, limit: int = 10
) -> List[List[Dict[str, str]]]:
    """Inline-keyboard rows listing the first episodes of a playlist."""
    rows: List[List[Dict[str, str]]] = []
    for index, entry in enumerate((analysis.get("entries") or [])[:limit]):
        title = str(entry.get("title") or entry.get("id") or index + 1)
        rows.append(
            [{"text": f"{index + 1}. {title}"[:60], "data": f"ep|{index}|{plan_id}"[:64]}]
        )
    return rows


def format_progress(job: Dict[str, Any]) -> str:
    """Progress line shown while a download runs."""
    status = job.get("status")
    if status == "completed":
        return f"Done: {job.get('filename') or 'file saved'}"
    if status == "error":
        return f"Failed: {job.get('error') or 'unknown error'}"
    if status == "cancelled":
        return "Cancelled."

    percent = float(job.get("progress") or 0)
    bar_length = 20
    filled = int(bar_length * percent / 100)
    bar = "#" * filled + "-" * (bar_length - filled)

    parts = [f"[{bar}] {percent:.1f}%"]
    if job.get("downloaded_bytes"):
        parts.append(format_size(job.get("downloaded_bytes")))
        if job.get("total_bytes"):
            parts[-1] += f" / {format_size(job.get('total_bytes'))}"
    if job.get("speed"):
        parts.append(f"{format_size(job.get('speed'))}/s")
    if job.get("eta") is not None:
        parts.append(f"ETA {int(job.get('eta'))}s")
    parts.append(str(job.get("stage") or "working..."))
    return " ".join(parts)


def can_upload(size: Optional[float], limit_mb: int = TELEGRAM_UPLOAD_LIMIT_MB) -> bool:
    """True when the file fits into a normal Bot API upload."""
    if not size:
        return True
    return size <= limit_mb * 1024 * 1024


def format_caption(job: Dict[str, Any]) -> str:
    """Caption attached to the uploaded file."""
    title = job.get("title") or job.get("filename") or "Download"
    caption = str(title)
    if job.get("quality_label"):
        caption += f"\n{job['quality_label']}"
    if job.get("filename"):
        caption += f"\n{job['filename']}"
    return caption[:1024]


# --------------------------------------------------------------------------- #
# the bot
# --------------------------------------------------------------------------- #


class TelegramDownloadBot:
    """Wires Telegram handlers to the shared job manager."""

    def __init__(self, manager: JobManager, web_url: str = "") -> None:
        self.manager = manager
        self.web_url = web_url.rstrip("/")
        self.application: Optional[Any] = None

    # -- helpers ---------------------------------------------------------- #
    def _authorised(self, update: Any) -> bool:
        allowed = config.TELEGRAM_ALLOWED_CHATS
        if not allowed:
            return True
        chat = getattr(update, "effective_chat", None)
        chat_id = str(getattr(chat, "id", ""))
        return chat_id in allowed

    async def _reply(self, update: Any, text: str, reply_markup: Any = None) -> None:
        message = getattr(update, "effective_message", None)
        if message is None:
            return
        await message.reply_text(text, reply_markup=reply_markup)

    # -- commands --------------------------------------------------------- #
    async def on_start(self, update: Any, context: Any) -> None:
        if not self._authorised(update):
            return
        await self._reply(
            update,
            "Send me a link and I will download it for you.\n\n"
            "I support 1,700+ sites, including toonworld4all.me, YouTube, TikTok, "
            "X/Twitter, Instagram and more.\n\n"
            "Commands:\n"
            "/help - how to use me\n"
            "/sites - how many sites are supported\n"
            "/history - your recent downloads",
        )

    async def on_help(self, update: Any, context: Any) -> None:
        if not self._authorised(update):
            return
        await self._reply(
            update,
            "How to use me:\n\n"
            "1. Send a link (or a message containing a link).\n"
            "2. Pick a quality from the buttons.\n"
            "3. I download it and upload the file here.\n\n"
            "Tip: for a playlist, send the season link and choose an episode.",
        )

    async def on_sites(self, update: Any, context: Any) -> None:
        if not self._authorised(update):
            return
        from .ytdlp_client import supported_extractors

        names = await asyncio.to_thread(supported_extractors)
        await self._reply(update, f"I can handle {len(names)} different sites.")

    async def on_history(self, update: Any, context: Any) -> None:
        if not self._authorised(update):
            return
        history = self.manager.history(limit=10)
        if not history:
            await self._reply(update, "No downloads yet.")
            return
        lines = ["Your recent downloads:"]
        for item in history:
            lines.append(f"- {item.get('title') or item.get('id')} [{item.get('status')}]")
        await self._reply(update, "\n".join(lines))

    # -- link handling ---------------------------------------------------- #
    async def on_message(self, update: Any, context: Any) -> None:
        if not self._authorised(update):
            return
        message = getattr(update, "effective_message", None)
        if message is None or not message.text:
            return
        url = extract_url(message.text)
        if not url:
            return

        status = await message.reply_text("Analysing the link...")
        try:
            analysis = await asyncio.to_thread(self.manager.analyse, url)
        except Exception as exc:  # noqa: BLE001
            await status.edit_text(f"Could not read that link.\n\n{exc}")
            return

        plan_id = self.manager.store_plan(analysis)
        summary = describe_analysis(analysis)

        if analysis.get("kind") == "playlist":
            rows = build_episode_buttons(analysis, plan_id)
            keyboard = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton(button["text"], callback_data=button["data"]) for button in row]
                    for row in rows
                ]
            )
            await status.edit_text(f"{summary}\n\nPick an episode:", reply_markup=keyboard)
            return

        rows = build_quality_buttons(analysis, plan_id)
        if not rows:
            await status.edit_text("That link has no downloadable formats.")
            return
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(button["text"], callback_data=button["data"]) for button in row]
                for row in rows
            ]
        )
        await status.edit_text(f"{summary}\n\nPick a quality:", reply_markup=keyboard)

    async def on_quality(self, update: Any, context: Any) -> None:
        query = getattr(update, "callback_query", None)
        if query is None:
            return
        if not self._authorised(update):
            await query.answer("Not authorised.", show_alert=True)
            return

        await query.answer()
        parts = (query.data or "").split("|")
        if len(parts) != 3:
            return
        kind, value, plan_id = parts

        plan = self.manager.get_plan(plan_id)
        if not plan:
            await query.edit_message_text("That offer expired - send the link again.")
            return

        if kind == "ep":
            entries = plan.get("entries") or []
            index = int(value)
            if index >= len(entries):
                await query.edit_message_text("That episode is no longer available.")
                return
            entry = entries[index]
            url = entry.get("url")
            title = entry.get("title")
            analysis = await asyncio.to_thread(self.manager.analyse, url)
            plan_id = self.manager.store_plan(analysis)
            quality = next(
                (q for q in analysis.get("qualities", []) if q.get("id") == "best"), None
            ) or (analysis.get("qualities") or [{}])[0]
        else:
            analysis = plan
            url = plan.get("webpage_url") or plan.get("url")
            title = plan.get("title")
            quality = next(
                (q for q in analysis.get("qualities", []) if q.get("id") == value), None
            )
            if quality is None:
                await query.edit_message_text("That quality is no longer available.")
                return

        message = await query.edit_message_text(f"Starting download: {title or 'file'}")
        job = self.manager.create_from_analysis(
            url,
            analysis,
            quality_id=quality.get("id", "best"),
            mode="audio" if quality.get("kind") == "audio" else "video",
        )
        await self._follow(job.id, message)

    async def on_cancel(self, update: Any, context: Any) -> None:
        if not self._authorised(update):
            return
        message = getattr(update, "effective_message", None)
        if message is None or not message.text:
            return
        job_id = message.text.split()[-1] if message.text.split() else ""
        if self.manager.cancel(job_id):
            await self._reply(update, "Cancelling...")
        else:
            await self._reply(update, "No running download with that id.")

    # -- progress --------------------------------------------------------- #
    async def _follow(self, job_id: str, message: Any) -> None:
        last = ""
        while True:
            job = self.manager.get(job_id)
            if job is None:
                break
            snapshot = job.to_dict()
            line = format_progress(snapshot)
            if line != last:
                last = line
                try:
                    await message.edit_text(line)
                except Exception:  # pragma: no cover - message may be gone
                    pass
            if snapshot["status"] in {"completed", "error", "cancelled"}:
                break
            await asyncio.sleep(2.0)

        job = self.manager.get(job_id)
        if job is None:
            return
        snapshot = job.to_dict()

        if snapshot["status"] != "completed":
            await message.edit_text(format_progress(snapshot))
            return

        # Use the job object for the path: the public snapshot deliberately
        # hides server-side paths.
        path = Path(job.filepath or "")
        if not job.filepath or not path.exists():
            await message.edit_text("The file disappeared before it could be sent.")
            return

        size = snapshot.get("total_bytes") or path.stat().st_size
        if not can_upload(size, config.TELEGRAM_MAX_UPLOAD_MB):
            await message.edit_text(
                f"The file is {format_size(size)}, which is larger than the "
                f"{config.TELEGRAM_MAX_UPLOAD_MB} MB Telegram upload limit.\n\n"
                f"Grab it from the web interface instead:"
                + (f"\n{self.web_url}/api/jobs/{snapshot['id']}/file" if self.web_url else "")
            )
            return

        caption = format_caption(snapshot)
        try:
            if path.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".avi"}:
                await message.reply_video(
                    video=str(path),
                    caption=caption,
                    supports_streaming=True,
                )
            else:
                await message.reply_document(document=str(path), caption=caption)
            await message.edit_text("Sent.")
        except Exception as exc:  # noqa: BLE001
            await message.edit_text(f"Could not upload the file: {exc}")


# --------------------------------------------------------------------------- #
# lifecycle helpers
# --------------------------------------------------------------------------- #


def build_application(manager: JobManager, web_url: str = "") -> Optional[Any]:
    """Create the Telegram application, or ``None`` when unavailable."""
    if not TELEGRAM_AVAILABLE or not config.TELEGRAM_BOT_TOKEN:
        return None

    bot = TelegramDownloadBot(manager, web_url=web_url)
    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", bot.on_start))
    application.add_handler(CommandHandler("help", bot.on_help))
    application.add_handler(CommandHandler("sites", bot.on_sites))
    application.add_handler(CommandHandler("history", bot.on_history))
    application.add_handler(CommandHandler("cancel", bot.on_cancel))
    application.add_handler(CallbackQueryHandler(bot.on_quality))
    application.add_handler(CommandHandler("download", bot.on_message))

    bot.application = application
    return bot.application


async def start_bot(manager: JobManager, web_url: str = "") -> Optional[Any]:
    """Start long polling.  Returns the application so it can be stopped."""
    application = build_application(manager, web_url=web_url)
    if application is None:
        if config.TELEGRAM_BOT_TOKEN and not TELEGRAM_AVAILABLE:
            log.warning("TELEGRAM_BOT_TOKEN is set but python-telegram-bot is not installed")
        return None

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    log.info("Telegram bot is polling")
    return application


async def stop_bot(application: Optional[Any]) -> None:
    """Stop a bot started by :func:`start_bot`."""
    if application is None:
        return
    try:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
    except Exception:  # pragma: no cover - best effort
        pass
