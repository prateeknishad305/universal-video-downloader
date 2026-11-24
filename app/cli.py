#!/usr/bin/env python
"""Command-line downloader for headless / scripted use.

Examples
--------
    python -m app.cli "https://www.youtube.com/watch?v=XXXX" --quality best
    python -m app.cli -f urls.txt --audio
    python -m app.cli "URL" --list-formats
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from . import config
from .ytdlp_client import (
    CancelledError,
    UnsupportedSiteError,
    analyze,
    download_media,
    safe_filename,
)


def human_bytes(value: Optional[float]) -> str:
    if not value:
        return "?"
    units = ["B", "KB", "MB", "GB", "TB"]
    number = float(value)
    index = 0
    while number >= 1024 and index < len(units) - 1:
        number /= 1024
        index += 1
    return f"{number:.1f} {units[index]}"


def list_formats(url: str) -> int:
    info = analyze(url)
    print(f"\n  {info['title']}")
    print(f"  by {info.get('uploader') or 'unknown'}  |  {info.get('extractor') or '?'}\n")
    print(f"  {'ID':<16}{'RES':<10}{'EXT':<8}{'SIZE':>12}  NOTE")
    print("  " + "-" * 62)
    for fmt in info.get("formats", []):
        resolution = f"{fmt.get('height')}p" if fmt.get("height") else "audio"
        size = human_bytes(fmt.get("filesize"))
        note = fmt.get("format_note") or ""
        print(f"  {str(fmt.get('format_id')):<16}{resolution:<10}{str(fmt.get('ext')):<8}{size:>12}  {note}")
    print()
    return 0


def pick_quality(info, wanted: str) -> dict:
    qualities = info.get("qualities") or []
    for quality in qualities:
        if quality["id"] == wanted:
            return quality
    return next((q for q in qualities if q["id"] == "best"), qualities[0])


def download_one(url: str, quality_id: str, audio_only: bool, subtitles: bool) -> int:
    info = analyze(url)
    if info.get("kind") == "playlist":
        print(f"  '{info['title']}' is a playlist with {info['playlist_count']} items.")
        print("  Use the web UI to pick individual videos.")
        return 1

    quality = pick_quality(info, "audio_mp3" if audio_only else quality_id)
    mode = "audio" if quality.get("kind") == "audio" else "video"
    stem = safe_filename(info.get("title") or "download")
    outtmpl = str(config.DOWNLOAD_DIR / f"{stem}.%(ext)s")

    print(f"  Title   : {info.get('title')}")
    print(f"  Quality : {quality.get('label')}")
    print(f"  Saving  : {outtmpl}")

    state = {"last": 0.0}

    def on_progress(data):
        if data.get("phase") == "processing":
            print("\r  merging / post-processing...      ", end="", flush=True)
            return
        total = data.get("total_bytes") or 0
        done = data.get("downloaded_bytes") or 0
        percent = (done / total * 100) if total else 0
        speed = human_bytes(data.get("speed"))
        print(
            f"\r  {percent:5.1f}%  {human_bytes(done)}/{human_bytes(total)}  {speed}/s   ",
            end="",
            flush=True,
        )
        state["last"] = percent

    def on_log(level, message):
        if level in {"warning", "error"}:
            print(f"\n  [{level}] {message}")

    path = download_media(
        url,
        outtmpl=outtmpl,
        selector=quality["selector"],
        mode=mode,
        subtitles=subtitles,
        on_progress=on_progress,
        on_log=on_log,
    )
    print(f"\r  done -> {path}                    ")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="app.cli", description="Download video or audio from any supported site."
    )
    parser.add_argument("url", nargs="*", help="one or more URLs to download")
    parser.add_argument("-f", "--file", help="text file containing URLs (one per line)")
    parser.add_argument(
        "-q", "--quality", default="best",
        help="quality id from the ladder: best, v1080, v720, ... (default: best)",
    )
    parser.add_argument("--audio", action="store_true", help="download audio only as MP3")
    parser.add_argument("--subs", action="store_true", help="embed subtitles when available")
    parser.add_argument("--list-formats", action="store_true", help="show the available formats and exit")
    parser.add_argument("--out", help="output directory (default: ./data/downloads)")
    args = parser.parse_args(argv)

    if args.out:
        config.DOWNLOAD_DIR = Path(args.out).expanduser().resolve()
        config.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    urls = list(args.url)
    if args.file:
        urls += [
            line.strip()
            for line in Path(args.file).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
    if not urls:
        parser.print_help()
        return 0

    failures = 0
    for url in urls:
        print(f"\n>> {url}")
        try:
            if args.list_formats:
                failures += list_formats(url)
            else:
                failures += download_one(url, args.quality, args.audio, args.subs)
        except UnsupportedSiteError as exc:
            print(f"  error: {exc}")
            failures += 1
        except CancelledError:
            print("  cancelled")
            failures += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  error: {exc}")
            failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
