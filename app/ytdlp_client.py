"""Thin, well-behaved wrapper around yt-dlp.

yt-dlp already understands ~1,800 different sites (YouTube, TikTok, Twitter/X,
Instagram, Vimeo, Dailymotion, Bilibili, SoundCloud, Twitch, Reddit, Pinterest,
Facebook, Rumble, OK.ru, Niconico, and hundreds more).  This module turns that
library into three predictable operations:

* :func:`analyze`  -- inspect a URL and describe what can be downloaded.
* :func:`download_media` -- fetch one media item with live progress callbacks.
* :func:`supported_extractors` -- report which site plugins are available.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import yt_dlp
from yt_dlp.utils import DownloadError

from . import config
from .sources import multimovies, toonworld

__all__ = [
    "CancelledError",
    "UnsupportedSiteError",
    "analyze",
    "download_media",
    "supported_extractors",
    "detect_extractor",
    "safe_filename",
]


class CancelledError(Exception):
    """Raised internally when a user cancels an in-flight download."""


class UnsupportedSiteError(Exception):
    """Raised when no yt-dlp extractor recognises the supplied URL."""


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

_UNSAFE_CHARS = re.compile(r"[^\w\-. ]+")
_MULTISPACE = re.compile(r"\s+")
_MULTI_DASH = re.compile(r"-{2,}")


def safe_filename(name: str, max_length: int = 120) -> str:
    """Return a filesystem-safe stem for *name*.

    Path separators and traversal sequences are stripped, so the result is
    always a single filename component.
    """
    cleaned = _UNSAFE_CHARS.sub(" ", name or "")
    cleaned = _MULTISPACE.sub(" ", cleaned).strip(" .")
    cleaned = _MULTI_DASH.sub("-", cleaned)
    if not cleaned:
        cleaned = "download"
    return cleaned[:max_length].strip(" .") or "download"


def _human_bytes(value: Optional[float]) -> Optional[float]:
    return float(value) if value else None


class CollectingLogger:
    """A yt-dlp logger that funnels messages into a callback instead of stdout."""

    def __init__(self, sink: Optional[Callable[[str, str], None]] = None) -> None:
        self._sink = sink

    def debug(self, msg: str) -> None:  # noqa: D102 - yt-dlp Logger interface
        return  # yt-dlp is extremely chatty at debug level; ignore it.

    def info(self, msg: str) -> None:
        self._emit("info", msg)

    def warning(self, msg: str) -> None:
        self._emit("warning", msg)

    def error(self, msg: str) -> None:
        self._emit("error", msg)

    def _emit(self, level: str, msg: str) -> None:
        if self._sink is None:
            return
        try:
            self._sink(level, str(msg))
        except Exception:  # pragma: no cover - logging must never break a download
            pass


def _impersonate_target(name: str) -> Any:
    """Build the structured impersonation target yt-dlp expects."""
    try:
        from yt_dlp.networking.impersonate import ImpersonateTarget

        return ImpersonateTarget.from_str(name)
    except Exception:
        return None


def base_opts(impersonate: Optional[str] = None) -> Dict[str, Any]:
    """Common yt-dlp options shared by probing and downloading."""
    opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "ignoreerrors": False,
        "noplaylist": False,
        "socket_timeout": config.REQUEST_TIMEOUT,
        "retries": 10,
        "fragment_retries": 10,
        "file_access_retries": 3,
        "concurrent_fragment_downloads": 4,
        "continuedl": True,
        "overwrites": False,
    }
    if impersonate and config.IMPERSONATION_AVAILABLE:
        # The Python API expects an ImpersonateTarget object; the command line
        # converts the plain string for us.
        target = _impersonate_target(impersonate)
        if target is not None:
            opts["impersonate"] = target
    if config.PROXY:
        opts["proxy"] = config.PROXY
    if config.COOKIES_FILE:
        opts["cookiefile"] = config.COOKIES_FILE
    if config.COOKIES_FROM_BROWSER:
        opts["cookiesfrombrowser"] = (config.COOKIES_FROM_BROWSER,)
    return opts


def _pick_thumbnail(info: Dict[str, Any]) -> Optional[str]:
    thumbs = info.get("thumbnails") or []
    if not thumbs:
        return info.get("thumbnail")
    best = max(thumbs, key=lambda t: (t.get("preference") or 0, t.get("width") or 0))
    return best.get("url") or info.get("thumbnail")


def _format_to_dict(fmt: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise a raw yt-dlp format entry for the frontend."""
    size = fmt.get("filesize") or fmt.get("filesize_approx")
    return {
        "format_id": fmt.get("format_id"),
        "ext": fmt.get("ext"),
        "height": fmt.get("height"),
        "fps": fmt.get("fps"),
        "vcodec": fmt.get("vcodec") or "none",
        "acodec": fmt.get("acodec") or "none",
        "filesize": size,
        "tbr": fmt.get("tbr"),
        "protocol": fmt.get("protocol"),
        "format_note": fmt.get("format_note"),
        "dynamic_range": fmt.get("dynamic_range"),
        "language": fmt.get("language"),
        "container": fmt.get("container"),
    }


def _best_audio_size(formats: List[Dict[str, Any]]) -> int:
    sizes = [
        f.get("filesize") or f.get("filesize_approx") or 0
        for f in formats
        if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")
    ]
    return max(sizes) if sizes else 0


def _best_video_size(formats: List[Dict[str, Any]], height: Optional[int]) -> int:
    candidates = [
        f.get("filesize") or f.get("filesize_approx") or 0
        for f in formats
        if f.get("vcodec") not in (None, "none")
        and (height is None or f.get("height") == height)
    ]
    return max(candidates) if candidates else 0


def build_quality_ladder(formats: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build a short, human-friendly quality menu from the raw format list."""
    ladder: List[Dict[str, Any]] = []

    combined = [
        f
        for f in formats
        if f.get("vcodec") not in (None, "none") and f.get("acodec") not in (None, "none")
    ]
    best_combined = max(
        combined,
        key=lambda f: (f.get("height") or 0, f.get("tbr") or 0),
        default=None,
    )
    best_video = max(
        (f for f in formats if f.get("vcodec") not in (None, "none")),
        key=lambda f: (f.get("height") or 0, f.get("tbr") or 0),
        default=None,
    )
    best_height = (best_combined or best_video or {}).get("height")
    best_size = (best_combined or {}).get("filesize") or (
        (_best_video_size(formats, best_height) + _best_audio_size(formats)) or None
    )
    ladder.append(
        {
            "id": "best",
            "label": "Best quality (original file, no re-encode)",
            "hint": "Picks the highest quality the site provides, merging video and audio with "
            "ffmpeg when the site delivers them separately.",
            "selector": "bestvideo+bestaudio/best",
            "kind": "combined",
            "height": best_height,
            "hdr": (best_combined or best_video or {}).get("dynamic_range") not in (None, "SDR"),
            "size": best_size,
            "requires_merge": best_combined is None,
        }
    )

    heights: Dict[int, str] = {}
    for fmt in formats:
        height = fmt.get("height")
        if not height:
            continue
        dynamic_range = fmt.get("dynamic_range") or "SDR"
        current = heights.get(height)
        if current is None or (current == "SDR" and dynamic_range != "SDR"):
            heights[height] = dynamic_range

    audio_size = _best_audio_size(formats)
    for height in sorted(heights, reverse=True):
        dynamic_range = heights[height]
        video_size = _best_video_size(formats, height)
        estimate = (video_size + audio_size) or None
        ladder.append(
            {
                "id": f"v{height}",
                "label": f"{height}p" + (" HDR" if dynamic_range != "SDR" else ""),
                "hint": "Best video + best audio, merged with ffmpeg.",
                "selector": f"bestvideo[height<={height}]+bestaudio/best[height<={height}]",
                "kind": "merged",
                "height": height,
                "hdr": dynamic_range != "SDR",
                "size": estimate,
                "requires_merge": True,
            }
        )

    ladder.append(
        {
            "id": "audio_mp3",
            "label": "Audio only - MP3 (192 kbps)",
            "hint": "Extracts the audio track and converts it to MP3.",
            "selector": "bestaudio/best",
            "kind": "audio",
            "height": None,
            "hdr": False,
            "size": audio_size or None,
            "requires_merge": False,
        }
    )
    ladder.append(
        {
            "id": "audio_original",
            "label": "Audio only - original format",
            "hint": "Keeps the source codec (m4a, opus, wav...).",
            "selector": "bestaudio/best",
            "kind": "audio",
            "height": None,
            "hdr": False,
            "size": audio_size or None,
            "requires_merge": False,
        }
    )
    return ladder


# --------------------------------------------------------------------------- #
# analysis
# --------------------------------------------------------------------------- #


def _iter_extractors():
    """Yield every extractor class shipped with the installed yt-dlp."""
    try:
        from yt_dlp.extractor import gen_extractors  # yt-dlp >= 2024

        yield from gen_extractors()
        return
    except Exception:
        pass
    try:  # older layout
        from yt_dlp.extractor.gen_extractors import gen_extractors

        yield from gen_extractors()
    except Exception:
        return


def detect_extractor(url: str) -> Optional[str]:
    """Return the name of the yt-dlp extractor that handles *url*."""
    for ie in _iter_extractors():
        try:
            if ie.suitable(url):
                name = getattr(ie, "IE_NAME", None)
                return name or getattr(ie, "__name__", None) or ie.__class__.__name__
        except Exception:
            continue
    return None


def supported_extractors() -> List[str]:
    """Names of every site plugin compiled into the installed yt-dlp."""
    names = set()
    for ie in _iter_extractors():
        name = getattr(ie, "IE_NAME", None)
        if name:
            names.add(name)
    return sorted(names)


def _extract_entries(info: Dict[str, Any]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for entry in info.get("entries") or []:
        if not entry:
            continue
        url = (
            entry.get("webpage_url")
            or entry.get("original_url")
            or entry.get("url")
        )
        if not url:
            continue
        entries.append(
            {
                "id": entry.get("id"),
                "title": entry.get("title") or entry.get("id") or url,
                "url": url,
                "duration": entry.get("duration"),
                "thumbnail": entry.get("thumbnail"),
                "uploader": entry.get("uploader") or entry.get("channel"),
                "playlist_title": entry.get("playlist_title"),
            }
        )
    return entries


def analyze(url: str) -> Dict[str, Any]:
    """Inspect *url* and describe the media that is available.

    Returns a dictionary with either a ``formats``/``qualities`` pair (single
    video) or an ``entries`` list (playlist / album / channel).
    """
    url = url.strip()
    for source in (toonworld, multimovies):
        if source.matches(url):
            try:
                return source.probe(url)
            except Exception as exc:  # noqa: BLE001
                raise DownloadError(str(exc)) from exc

    opts = base_opts()
    opts.update(
        {
            "skip_download": True,
            "extract_flat": "in_playlist",
            "logger": CollectingLogger(),
        }
    )

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as exc:
        message = str(exc)
        if "Unsupported URL" in message:
            raise UnsupportedSiteError(
                "No downloader plugin recognises that URL. It may be an unsupported "
                "site, or the link may be broken."
            ) from exc
        raise

    if not info:
        raise DownloadError("No information could be extracted from that URL.")

    # --- playlist / album / channel ------------------------------------- #
    if info.get("_type") in {"playlist", "multi_video"} or "entries" in info:
        entries = _extract_entries(info)
        return {
            "kind": "playlist",
            "id": info.get("id"),
            "title": info.get("title") or "Playlist",
            "uploader": info.get("uploader") or info.get("channel"),
            "thumbnail": _pick_thumbnail(info),
            "webpage_url": info.get("webpage_url") or url,
            "playlist_count": len(entries),
            "entries": entries,
            "extractor": detect_extractor(url),
        }

    # --- single video ---------------------------------------------------- #
    raw_formats = info.get("formats") or []
    formats = [_format_to_dict(f) for f in raw_formats]
    qualities = build_quality_ladder(raw_formats)
    duration = info.get("duration")

    return {
        "kind": "video",
        "id": info.get("id"),
        "title": info.get("title") or "Untitled",
        "uploader": info.get("uploader") or info.get("channel"),
        "thumbnail": _pick_thumbnail(info),
        "duration": duration,
        "is_live": bool(info.get("is_live")),
        "webpage_url": info.get("webpage_url") or url,
        "extractor": detect_extractor(url),
        "ext": info.get("ext"),
        "filesize": info.get("filesize") or info.get("filesize_approx"),
        "formats": formats,
        "qualities": qualities,
        "playlist_count": 0,
    }


# --------------------------------------------------------------------------- #
# downloading
# --------------------------------------------------------------------------- #


def download_media(
    url: str,
    *,
    outtmpl: str,
    selector: str,
    mode: str = "video",
    subtitles: bool = False,
    on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
    on_log: Optional[Callable[[str, str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> Path:
    """Download one media item and return the path of the finished file.

    ``selector`` is either a regular yt-dlp format selector or a
    ``toonworld:<quality-id>`` selector, in which case every host offered for
    that quality is tried in turn until one delivers the file.
    """
    if selector.startswith("toonworld:"):
        return _download_toonworld(
            url,
            selector.split(":", 1)[1],
            outtmpl=outtmpl,
            mode=mode,
            subtitles=subtitles,
            on_progress=on_progress,
            on_log=on_log,
            should_cancel=should_cancel,
        )
    if selector.startswith("multimovies:"):
        return _download_multimovies(
            url,
            selector.split(":", 1)[1],
            outtmpl=outtmpl,
            mode=mode,
            subtitles=subtitles,
            on_progress=on_progress,
            on_log=on_log,
            should_cancel=should_cancel,
        )
    return _download_direct(
        url,
        outtmpl=outtmpl,
        selector=selector,
        mode=mode,
        subtitles=subtitles,
        on_progress=on_progress,
        on_log=on_log,
        should_cancel=should_cancel,
    )


def _download_source_list(
    candidates: List[Dict[str, Any]],
    *,
    outtmpl: str,
    mode: str,
    subtitles: bool,
    on_progress: Optional[Callable[[Dict[str, Any]], None]],
    on_log: Optional[Callable[[str, str], None]],
    should_cancel: Optional[Callable[[], bool]],
    label: str = "host",
) -> Path:
    """Try each candidate source in turn until one delivers the file."""
    failures: List[str] = []

    for source in candidates:
        if should_cancel and should_cancel():
            raise CancelledError("Download cancelled by the user.")
        host = source.get("host") or label
        destination = source.get("url")
        detail = source.get("detail") or ""
        if not destination:
            failures.append(f"{host}: {detail or 'no destination'}")
            if on_log:
                on_log("warning", f"{host}: {detail or 'no destination'}")
            continue
        if source.get("state") in {"missing", "blocked"}:
            failures.append(f"{host}: {detail}")
            if on_log:
                on_log("warning", f"{host} skipped - {detail}")
            continue
        if on_log:
            on_log("info", f"Trying {host}: {destination}")
        try:
            if source.get("kind") == "mega":
                path = _download_mega(source, outtmpl=outtmpl, on_log=on_log)
            else:
                path = _download_direct(
                    destination,
                    outtmpl=outtmpl,
                    selector="bestvideo+bestaudio/best",
                    mode=mode,
                    subtitles=subtitles,
                    on_progress=on_progress,
                    on_log=on_log,
                    should_cancel=should_cancel,
                    impersonate="chrome",
                    referer=source.get("referer"),
                )
            if source.get("expected_size") is not None:
                problem = verify_media_file(path, source.get("expected_size"))
                if problem:
                    raise DownloadError(problem)
            return path
        except CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            message = str(exc).strip() or exc.__class__.__name__
            if "Unsupported URL" in message:
                message = (
                    "the host page did not expose a direct file URL "
                    "(the archive id looks like a placeholder)"
                )
            lowered = destination.lower()
            is_playlist = ".m3u8" in lowered or lowered.split("?")[0].endswith(".txt")
            if is_playlist and not source.get("ffmpeg_tried"):
                if on_log:
                    on_log("info", f"Retrying {host} with the ffmpeg downloader")
                try:
                    return _download_direct(
                        destination,
                        outtmpl=outtmpl,
                        selector="best",
                        mode=mode,
                        subtitles=subtitles,
                        on_progress=on_progress,
                        on_log=on_log,
                        should_cancel=should_cancel,
                        impersonate="chrome",
                        use_ffmpeg=True,
                        referer=source.get("referer"),
                    )
                except CancelledError:
                    raise
                except Exception as retry_exc:  # noqa: BLE001
                    message = str(retry_exc).strip() or retry_exc.__class__.__name__
            failures.append(f"{host}: {message}")
            if on_log:
                on_log("warning", f"{host} failed: {message}")

    detail = " | ".join(failures) if failures else "no sources were offered"
    raise DownloadError(f"Every download source failed for this item. {detail}")


def _download_multimovies(
    url: str,
    quality_id: str,
    *,
    outtmpl: str,
    mode: str = "video",
    subtitles: bool = False,
    on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
    on_log: Optional[Callable[[str, str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> Path:
    """Download a MultiMovies episode, failing over between streaming servers."""
    candidates = multimovies.resolve_sources(url, quality_id)
    return _download_source_list(
        candidates,
        outtmpl=outtmpl,
        mode=mode,
        subtitles=subtitles,
        on_progress=on_progress,
        on_log=on_log,
        should_cancel=should_cancel,
        label="server",
    )


def _download_toonworld(
    url: str,
    quality_id: str,
    *,
    outtmpl: str,
    mode: str = "video",
    subtitles: bool = False,
    on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
    on_log: Optional[Callable[[str, str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> Path:
    """Try every host for a toonworld4all.me quality until one works."""
    sources = toonworld.resolve_sources(url, quality_id)
    return _download_source_list(
        sources,
        outtmpl=outtmpl,
        mode=mode,
        subtitles=subtitles,
        on_progress=on_progress,
        on_log=on_log,
        should_cancel=should_cancel,
        label="host",
    )


def verify_media_file(path: Path, expected_size: Optional[int] = None) -> Optional[str]:
    """Return a complaint when *path* is not a usable media file."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        return f"the downloaded file could not be read: {exc}"
    if expected_size and size < expected_size * 0.05:
        return (
            f"the host returned {size} bytes instead of the {expected_size} bytes "
            "the archive announced"
        )
    if size < config.MIN_MEDIA_BYTES:
        return (
            f"the host returned a placeholder of {size} bytes instead of the episode"
        )
    try:
        with path.open("rb") as handle:
            head = handle.read(64)
    except OSError:
        head = b""
    if head.startswith(b"video-bytes"):
        return "the host returned a placeholder instead of the episode"
    problem = _probe_media_file(path)
    if problem:
        return problem
    return None


def _probe_media_file(path: Path) -> Optional[str]:
    """Check with ffprobe that the file really holds media."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration,format_name",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return "the downloaded file could not be read as media"
    output = (completed.stdout or b"").decode("utf-8", "replace").strip()
    if not output:
        return "the downloaded file holds no media"
    return None


def _download_mega(
    source: Dict[str, Any],
    *,
    outtmpl: str,
    on_log: Optional[Callable[[str, str], None]] = None,
) -> Path:
    """Download a MEGA file through the MEGA API."""
    from .sources import mega

    extra = source.get("extra") or {}
    handle = extra.get("handle")
    key = extra.get("key")
    if not handle or not key:
        raise DownloadError("the MEGA link did not carry a file key")

    template = Path(outtmpl)
    name = template.name
    if "{ext}" in name:
        name = name.replace("{ext}", "mp4")
    target = template.with_name(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    if on_log:
        on_log("info", f"Downloading from MEGA: {handle}")
    mega.download(handle, key, str(target))
    return target



def _download_direct(
    url: str,
    *,
    outtmpl: str,
    selector: str,
    mode: str = "video",
    subtitles: bool = False,
    on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
    on_log: Optional[Callable[[str, str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    impersonate: Optional[str] = None,
    use_ffmpeg: bool = False,
    referer: Optional[str] = None,
) -> Path:
    """Download straight from *url* with yt-dlp."""
    finished: List[Path] = []

    def progress_hook(data: Dict[str, Any]) -> None:
        if should_cancel and should_cancel():
            raise CancelledError("Download cancelled by the user.")
        status = data.get("status")
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            done = data.get("downloaded_bytes") or 0
            if on_progress:
                on_progress(
                    {
                        "downloaded_bytes": done,
                        "total_bytes": total,
                        "speed": data.get("speed"),
                        "eta": data.get("eta"),
                    }
                )
        elif status == "finished":
            filename = data.get("filename")
            if filename:
                finished.append(Path(filename))
            if on_progress:
                on_progress(
                    {
                        "downloaded_bytes": data.get("total_bytes") or 0,
                        "total_bytes": data.get("total_bytes") or 0,
                        "speed": None,
                        "eta": None,
                        "phase": "processing",
                    }
                )
        elif status == "error":
            if on_log:
                on_log("error", str(data.get("error") or "Unknown download error"))

    def postprocessor_hook(data: Dict[str, Any]) -> None:
        status = data.get("status")
        name = data.get("postprocessor")
        if status == "started" and on_log:
            on_log("info", f"Post-processing: {name}")
        elif status == "finished" and on_log:
            on_log("info", f"Post-processing finished: {name}")

    def match_filter(_fmt: Dict[str, Any], *, incomplete: bool = False) -> None:
        if should_cancel and should_cancel():
            raise CancelledError("Download cancelled by the user.")

    opts = base_opts(impersonate=impersonate)
    opts.update(
        {
            "outtmpl": outtmpl,
            "format": selector,
            "progress_hooks": [progress_hook],
            "postprocessor_hooks": [postprocessor_hook],
            "match_filter": match_filter,
            "logger": CollectingLogger(on_log),
            "noplaylist": True,
            "windowsfilenames": True,
        }
    )

    if mode == "audio":
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]
    else:
        # ``mp4/mkv`` lets ffmpeg fall back to MKV when MP4 cannot hold the
        # codecs, instead of failing the whole download.
        opts["merge_output_format"] = "mp4/mkv"

    if referer:
        # Streaming hosts block requests that do not come from their player.
        headers = dict(opts.get("http_headers") or {})
        headers["Referer"] = referer
        opts["http_headers"] = headers

    # A playlist served as ".txt" is not recognised as HLS by the native
    # downloader, so ffmpeg reads it directly.
    if use_ffmpeg or url.lower().split("?")[0].endswith(".txt"):
        opts["downloader"] = "ffmpeg"
        opts["hls_use_mpegts"] = True
        opts["continuedl"] = True

    if subtitles:
        opts.update(
            {
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": ["en.*", "en"],
                "embedsubtitles": True,
            }
        )

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

    final_path = _resolve_final_path(info, finished)
    if final_path is None or not final_path.exists():
        raise DownloadError("The download finished but no output file could be located.")
    return final_path


def _resolve_final_path(info: Optional[Dict[str, Any]], finished: List[Path]) -> Optional[Path]:
    """Work out where the post-processed file ended up."""
    if info:
        requested = info.get("requested_downloads") or []
        for entry in requested:
            filepath = entry.get("filepath")
            if filepath and Path(filepath).exists():
                return Path(filepath)
        filepath = info.get("filepath")
        if filepath and Path(filepath).exists():
            return Path(filepath)
    for candidate in reversed(finished):
        if candidate.exists():
            return candidate
    return None


def find_partial_files(directory, stem: str) -> List[Path]:
    """Return partial (``*.part``) files belonging to *stem*."""
    directory = Path(directory)
    if not directory.exists():
        return []
    return [p for p in directory.glob(f"{stem}*") if p.suffix == ".part"]


def remove_partial_files(directory, stem: str) -> None:
    """Delete partial files left behind by a cancelled download."""
    for path in find_partial_files(directory, stem):
        try:
            path.unlink()
        except OSError:
            pass


def run_in_thread(fn, *args, **kwargs):
    """Run a blocking yt-dlp call in a worker thread."""
    import asyncio

    async def _runner():
        return await asyncio.to_thread(fn, *args, **kwargs)

    return _runner()


# Keep a reference so the module is import-safe when yt-dlp emits warnings.
_LOGGER_LOCK = threading.Lock()
