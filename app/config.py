"""Application configuration, driven by environment variables."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data")).resolve()
DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", DATA_DIR / "downloads")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# How many downloads may run at the same time.
MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", "3"))

# Network timeout (seconds) used for every HTTP request yt-dlp makes.
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "45"))

# Optional bearer-style token. When set, every /api route requires it.
API_TOKEN = (os.environ.get("API_TOKEN") or "").strip() or None

# Optional authentication aids for sites that require a logged-in session.
COOKIES_FROM_BROWSER = (os.environ.get("COOKIES_FROM_BROWSER") or "").strip() or None
COOKIES_FILE = (os.environ.get("COOKIES_FILE") or "").strip() or None

# Optional outbound proxy, e.g. http://127.0.0.1:8080
PROXY = (os.environ.get("PROXY") or "").strip() or None

# Whether to embed subtitles when the source provides them.
EMBED_SUBTITLES = (os.environ.get("EMBED_SUBTITLES") or "0").strip() in {"1", "true", "yes"}

# --- optional MongoDB job history ---------------------------------------- #
MONGO_URI = (os.environ.get("MONGO_URI") or "").strip() or None
MONGO_DB = (os.environ.get("MONGO_DB") or "universal_downloader").strip()
MONGO_COLLECTION = (os.environ.get("MONGO_COLLECTION") or "jobs").strip()

# --- optional Telegram bot ----------------------------------------------- #
TELEGRAM_BOT_TOKEN = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip() or None
TELEGRAM_ALLOWED_CHATS = {
    item.strip()
    for item in (os.environ.get("TELEGRAM_ALLOWED_CHATS") or "").split(",")
    if item.strip()
}
TELEGRAM_MAX_UPLOAD_MB = int(os.environ.get("TELEGRAM_MAX_UPLOAD_MB") or "50")

# Public base URL of this instance (used in Telegram messages).
PUBLIC_URL = (os.environ.get("PUBLIC_URL") or "").strip().rstrip("/")

# Browser impersonation (curl_cffi) clears most Cloudflare challenges.
IMPERSONATION_AVAILABLE = importlib.util.find_spec("curl_cffi") is not None

# Browser automation (Playwright) is used for links that are gated behind a
# JavaScript challenge.  The browser profile is kept between runs so that a
# clearance cookie survives a restart.
BROWSER_ENABLED = (os.environ.get("UVD_BROWSER_ENABLED") or "1").strip().lower() not in {
    "0",
    "false",
    "no",
}
BROWSER_EXECUTABLE = (os.environ.get("UVD_BROWSER_EXECUTABLE") or "").strip() or None
BROWSER_HEADLESS = (os.environ.get("UVD_BROWSER_HEADLESS") or "1").strip().lower() not in {
    "0",
    "false",
    "no",
}
BROWSER_TIMEOUT_MS = int(os.environ.get("UVD_BROWSER_TIMEOUT_MS", "45000"))
BROWSER_PROFILE_DIR = Path(
    os.environ.get("UVD_BROWSER_PROFILE") or (DATA_DIR / "browser-profile")
).resolve()

# A downloaded file smaller than this is treated as a placeholder instead of
# the requested media.  ffprobe checks the content on top of that.
MIN_MEDIA_BYTES = int(os.environ.get("UVD_MIN_MEDIA_BYTES", str(16 * 1024)))

# Largest file the downloader accepts, in MB.  0 means no limit.
MAX_FILE_MB = int(os.environ.get("UVD_MAX_FILE_MB", "0"))

# How many finished jobs are kept in memory before the oldest ones are dropped.
MAX_HISTORY_JOBS = int(os.environ.get("UVD_MAX_HISTORY_JOBS", "200"))

# Free space (MB) that has to stay available for a new download to start.
MIN_FREE_DISK_MB = int(os.environ.get("UVD_MIN_FREE_DISK_MB", "0"))

# How many threads ffmpeg may use.  A small container runs out of memory when
# ffmpeg decodes with every core it can see.
FFMPEG_THREADS = max(1, int(os.environ.get("UVD_FFMPEG_THREADS", "2")))

# The host the service runs on, used for the public links in the answers.
RENDER = bool(os.environ.get("RENDER"))

DEFAULT_HOST = os.environ.get("HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("PORT", "8000"))
