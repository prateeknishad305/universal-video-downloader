"""Resolve streaming embeds into real media URLs.

The sites that aggregate streaming providers embed them as iframes, and each
provider hides the actual media URL behind its own JavaScript.  Three
mechanisms are used, cheapest first:

1. **Deterministic provider APIs** - a few providers expose the manifest
   directly once you know the id, so no browser is needed.
2. **The provider's own JSON API** - the embed page carries a playback token
   that unlocks a ``/api/source/...`` endpoint returning the stream.
3. **A real browser** - the last resort.  The page is loaded in Chromium and
   the network is watched for media requests and for JSON responses that
   contain a media URL (providers that use MSE hide the playlist behind a
   ``blob:`` URL).

Whichever mechanism succeeds first wins; the rest are tried in turn.
"""

from __future__ import annotations

import glob
import json
import os
import re
from typing import Any, Dict, List, Optional

from . import http

MEDIA_RE = re.compile(r"\.(m3u8|mp4|m4v|webm|mkv|ts)(\?|$)", re.I)
# JSON keys that commonly hold a media URL.
MEDIA_KEYS = (
    "proxyurl",
    "m3u8",
    "file",
    "src",
    "url",
    "stream",
    "source",
    "manifest",
    "playlist",
    "hls",
)


class EmbedError(Exception):
    """Raised when an embed cannot be resolved into a media URL."""


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def find_chromium() -> Optional[str]:
    """Locate a usable Chromium build for Playwright."""
    patterns = [
        os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux/chrome"),
        os.path.expanduser(
            "~/.cache/ms-playwright/chromium_headless_shell-*/"
            "chrome-headless-shell-linux64/chrome-headless-shell"
        ),
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
    ]
    for pattern in patterns:
        for candidate in sorted(glob.glob(pattern)):
            if os.access(candidate, os.X_OK):
                return candidate
    return None


def playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401

        return True
    except Exception:
        return False


def extract_json_object(text: str, marker: str) -> Optional[Dict[str, Any]]:
    """Extract a JS object literal that follows *marker*.

    A regular expression stops at the first ``};`` which truncates nested
    objects, so the braces are matched properly instead.
    """
    index = text.find(marker)
    if index < 0:
        return None
    start = text.find("{", index + len(marker))
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for position in range(start, len(text)):
        char = text[position]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                raw = text[start : position + 1].replace("\\/", "/")
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return None
    return None


def _dig_media(value: Any, depth: int = 0) -> List[str]:
    """Pull media-looking URLs out of an arbitrary JSON structure."""
    found: List[str] = []
    if depth > 6:
        return found
    if isinstance(value, str):
        if value.startswith("http") and (MEDIA_RE.search(value) or "proxy?" in value):
            found.append(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str) and key.lower() in MEDIA_KEYS:
                if item.startswith("http"):
                    found.append(item)
            else:
                found.extend(_dig_media(item, depth + 1))
    elif isinstance(value, list):
        for item in value:
            found.extend(_dig_media(item, depth + 1))
    return found


# --------------------------------------------------------------------------- #
# deterministic provider resolvers
# --------------------------------------------------------------------------- #

# The VIDOUT player reads its manifests straight from a public JSON index.
VIDOUT_MANIFEST = (
    "https://raw.githubusercontent.com/Watchout2025/api/refs/heads/main/hls/{kind}/{id}/S{season}.json"
)


def resolve_vidout(url: str) -> List[str]:
    """VIDOUT publishes a plain JSON manifest per series and season."""
    match = re.search(r"/tv/(\d+)/s(\d+)/e(\d+)", url, re.I) or re.search(
        r"/tv/(\d+)/s(\d+)/e(\d+)", url
    )
    if not match:
        return []
    series_id, season = match.group(1), match.group(2)
    status, body, _ = http.fetch(
        VIDOUT_MANIFEST.format(kind="tv", id=series_id, season=season), timeout=30
    )
    if status != 200:
        return []
    try:
        manifest = json.loads(body)
    except json.JSONDecodeError:
        return []
    # The manifest maps episode number -> playlist; pick the requested one.
    episode = match.group(3)
    if episode and str(episode) in manifest:
        value = manifest[str(episode)]
        return [value] if isinstance(value, str) and value.startswith("http") else []
    return [
        value
        for value in manifest.values()
        if isinstance(value, str) and value.startswith("http")
    ]


def resolve_vidrift(url: str) -> List[str]:
    """Vidrift: read the playback token from the page, then ask for the source."""
    match = re.search(r"/embed/(tv|movie)/([^/]+)(?:/(\d+)(?:/(\d+))?)?", url, re.I)
    if not match:
        return []
    kind, ident = match.group(1).lower(), match.group(2)
    status, body, _ = http.fetch(url, timeout=45)
    if status != 200:
        return []
    meta = extract_json_object(body, "embedMeta")
    if not meta:
        return []

    token = meta.get("playbackToken")
    if not token:
        return []
    tmdb_id = meta.get("tmdbId")
    season = meta.get("season") or match.group(3) or 1
    episode = meta.get("episode") or match.group(4) or 1

    api = (
        f"https://embed.vidrift.net/api/source/{kind}/{tmdb_id}/{season}/{episode}"
        f"?token={token}&parent={meta.get('parent') or ''}"
    )
    status, body, _ = http.fetch(api, timeout=45, headers={"Accept": "application/json"})
    if status != 200:
        return []
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return []
    return _dig_media(payload)


def resolve_vidbolt(url: str) -> List[str]:
    """Vidbolt: the player asks ``/api/resolve`` for the stream."""
    status, body, _ = http.fetch(url, timeout=45)
    if status != 200:
        return []
    params = {}
    config = extract_json_object(body, "window.__VB__")
    if config:
        title = config.get("title") or {}
        params = {
            key: title[key]
            for key in ("id", "type", "season", "episode")
            if title.get(key)
        }
    if not params:
        for key, pattern in (
            ("id", r'"id"\s*:\s*"?([A-Za-z0-9_\-]+)"?'),
            ("type", r'"type"\s*:\s*"(tv|movie)"'),
            ("season", r'"season"\s*:\s*"?(\d+)"?'),
            ("episode", r'"episode"\s*:\s*"?(\d+)"?'),
        ):
            found = re.search(pattern, body)
            if found:
                params[key] = found.group(1)
    if not params:
        return []
    query = "&".join(f"{key}={value}" for key, value in params.items())
    api = f"https://vidbolt.xyz/api/resolve?{query}"
    # Vidbolt rejects API calls that do not come from its own player page.
    status, body, _ = http.fetch(
        api, timeout=45, headers={"Accept": "application/json", "Referer": url}
    )
    if status != 200:
        return []
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return []
    return _dig_media(payload)


DETERMINISTIC_RESOLVERS = (
    ("vidout", resolve_vidout),
    ("vidrift", resolve_vidrift),
    ("vidbolt", resolve_vidbolt),
)


# --------------------------------------------------------------------------- #
# browser fallback
# --------------------------------------------------------------------------- #


def resolve_with_browser(url: str, timeout: int = 60) -> List[str]:
    """Load the embed in Chromium and watch for the media URL."""
    if not playwright_available():
        return []
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return []

    candidates: List[str] = []
    executable = find_chromium()
    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                executable_path=executable,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--autoplay-policy=no-user-gesture-required",
                    "--mute-audio",
                ],
            )
            context = browser.new_context(
                user_agent=user_agent,
                viewport={"width": 1440, "height": 900},
                locale="en-US",
            )
            context.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            )
            page = context.new_page()

            def on_response(response):
                try:
                    response_url = response.url
                except Exception:
                    return
                if MEDIA_RE.search(response_url):
                    candidates.append(response_url)
                    return
                try:
                    content_type = (response.headers or {}).get("content-type", "")
                except Exception:
                    content_type = ""
                if "json" not in content_type.lower():
                    return
                try:
                    payload = response.json()
                except Exception:
                    return
                for found in _dig_media(payload):
                    candidates.append(found)

            page.on("response", on_response)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            except Exception:
                pass
            for _ in range(20):
                if "just a moment" not in (page.title() or "").lower():
                    break
                import time

                time.sleep(1)
            import time

            time.sleep(6)
            # Multi-hop embeds need a click on their play button, and the
            # media only starts flowing once the inner frame is alive.
            for _ in range(3):
                try:
                    page.evaluate(
                        """() => {
                            const buttons = Array.from(document.querySelectorAll(
                                'button,a[role=button],.play-button,[class*=play],[id*=play]'));
                            for (const b of buttons) {
                                const t = (b.textContent || '').trim().toLowerCase();
                                if (t.includes('play') || t.includes('watch') || t === '') {
                                    b.click();
                                    break;
                                }
                            }
                        }"""
                    )
                except Exception:
                    pass
                try:
                    page.evaluate(
                        "const v=document.querySelector('video');"
                        "if(v){v.muted=true;v.play().catch(()=>{});}"
                    )
                except Exception:
                    pass
                time.sleep(4)
            time.sleep(6)
            browser.close()
    except Exception:
        return []

    unique: List[str] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #


def resolve_embed(url: str, allow_browser: bool = True) -> Dict[str, Any]:
    """Resolve an embed URL into candidate media URLs.

    Returns ``{"media": [...], "method": "...", "error": None}``.
    """
    errors: List[str] = []

    for name, resolver in DETERMINISTIC_RESOLVERS:
        try:
            media = resolver(url)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
            continue
        if media:
            return {"media": media, "method": name, "error": None}

    if allow_browser:
        media = resolve_with_browser(url)
        if media:
            return {"media": media, "method": "browser", "error": None}
        errors.append("browser: no media request was captured")

    return {"media": [], "method": None, "error": "; ".join(errors) or "no resolver matched"}


def resolve_manifest(url: str, timeout: int = 60) -> List[Dict[str, Any]]:
    """Fetch an HLS master playlist and describe the qualities it offers."""
    status, body, headers = http.fetch(url, timeout=timeout, impersonate="chrome")
    if status != 200:
        raise EmbedError(f"The manifest returned HTTP {status}.")
    if "#EXTM3U" not in body:
        raise EmbedError("The manifest did not look like an HLS playlist.")

    variants: List[Dict[str, Any]] = []
    lines = body.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF"):
            continue
        resolution = re.search(r"RESOLUTION=(\d+)x(\d+)", line)
        bandwidth = re.search(r"BANDWIDTH=(\d+)", line)
        target = ""
        for candidate in lines[index + 1 :]:
            if candidate and not candidate.startswith("#"):
                target = candidate.strip()
                break
        variants.append(
            {
                "height": int(resolution.group(2)) if resolution else None,
                "width": int(resolution.group(1)) if resolution else None,
                "bandwidth": int(bandwidth.group(1)) if bandwidth else None,
                "url": target,
            }
        )
    if not variants:
        variants = [{"height": None, "width": None, "bandwidth": None, "url": url}]
    variants.sort(key=lambda item: item["height"] or 0, reverse=True)
    return variants
