"""Native support for toonworld4all.me.

The site is a WordPress front end that links every season page to a companion
*archive* app.  The archive app is a single-page application, but it server
renders its data into the page as a JSON blob (``window.__PROPS__``), which
makes the whole catalogue machine readable without executing any JavaScript:

* A season page lists one entry per episode, e.g.
  ``https://archive.toonworld4all.me/episode/naruto-shippuden-1x373``
* An episode page exposes ``metadata`` (title, episode number, air date,
  runtime, poster) and ``encodes`` -- one per quality, each holding the
  ``files`` that are offered for that quality on every supported host.

The ``link`` of each file is an encrypted redirect token.  The archive app
decodes it server side; asking it for a token with a *fresh* session returns
the destination page, which spells out the real file URL.

Supported hosts: HubCloud, Filepress, GDFlix and MEGA.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from . import http

SITE = "toonworld4all.me"
ARCHIVE = "archive.toonworld4all.me"

_EPISODE_RE = re.compile(
    rf"https?://{re.escape(ARCHIVE)}/episode/(?P<slug>[a-z0-9\-]+)", re.I
)
_PROPS_RE = re.compile(r"window\.__PROPS__\s*=\s*(\{.*?\});?\s*</script>", re.S)
_CODE_RE = re.compile(r'<code[^>]*>(.*?)</code>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_HEADING_RE = re.compile(
    r'mks_accordion_heading[^>]*><strong>(?P<title>[^<]+)</strong>.*?'
    r'href="(?P<url>https?://[^"]+/redirect/[a-f0-9]+)"',
    re.S,
)


class ToonWorldError(Exception):
    """Raised when a toonworld4all.me page cannot be parsed."""


def matches(url: str) -> bool:
    """True when *url* points at a page this source understands."""
    return bool(re.search(re.escape(SITE), url or "", re.I))


def _unwrap_props(html_text: str) -> Dict[str, Any]:
    match = _PROPS_RE.search(html_text)
    if not match:
        raise ToonWorldError("The page did not contain any episode data.")
    raw = match.group(1).replace('\\"', '"').replace("\\'", "'")
    import json

    try:
        props = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ToonWorldError("The episode data on the page could not be decoded.") from exc
    data = (props.get("data") or {}).get("data")
    if not data:
        raise ToonWorldError("The page did not contain any episode data.")
    return data


def _quality_slug(encode: Dict[str, Any]) -> str:
    resolution = (encode.get("resolution") or "video").lower()
    codec = (encode.get("codec") or "").lower().replace(".", "").replace(" ", "")
    slug = f"{resolution}-{codec}" if codec else resolution
    if encode.get("is_hq"):
        slug += "-hq"
    if encode.get("bit_depth"):
        slug += f"-{encode['bit_depth']}bit"
    return re.sub(r"[^a-z0-9\-]+", "-", slug).strip("-")


def _host_slug(host: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (host or "host").lower()).strip("-")


# --------------------------------------------------------------------------- #
# season pages
# --------------------------------------------------------------------------- #


def probe_season(url: str) -> Dict[str, Any]:
    """Turn a season page into a playlist of episodes."""
    status, body, _ = http.fetch(url)
    if status >= 400:
        raise ToonWorldError(f"The season page returned HTTP {status}.")

    entries: List[Dict[str, Any]] = []
    seen = set()
    for match in _HEADING_RE.finditer(body):
        episode_url = match.group("url")
        slug = _EPISODE_RE.search(episode_url)
        if not slug:
            continue
        key = slug.group("slug")
        if key in seen:
            continue
        seen.add(key)
        title = re.sub(r"\s+", " ", match.group("title")).strip()
        entries.append(
            {
                "id": key,
                "title": title,
                "url": f"https://{ARCHIVE}/episode/{key}",
                "duration": None,
                "thumbnail": None,
                "uploader": None,
            }
        )

    if not entries:
        # Fall back to a plain scan for episode links.
        for match in _EPISODE_RE.finditer(body):
            key = match.group("slug")
            if key in seen:
                continue
            seen.add(key)
            entries.append(
                {
                    "id": key,
                    "title": key.replace("-", " ").title(),
                    "url": f"https://{ARCHIVE}/episode/{key}",
                    "duration": None,
                    "thumbnail": None,
                    "uploader": None,
                }
            )

    if not entries:
        raise ToonWorldError("No episodes were found on that season page.")

    title = re.search(r"<title>(.*?)</title>", body, re.S)
    season_title = title.group(1).split("|")[0].strip() if title else "Season"
    season_title = re.sub(r"^Download\s+", "", season_title, flags=re.I)

    return {
        "kind": "playlist",
        "id": re.sub(r"[^a-z0-9]+", "-", url.lower())[-60:],
        "title": season_title,
        "uploader": "ToonWorld4All",
        "thumbnail": None,
        "webpage_url": url,
        "playlist_count": len(entries),
        "entries": entries,
        "extractor": "ToonWorld4All",
    }


# --------------------------------------------------------------------------- #
# episode pages
# --------------------------------------------------------------------------- #


def probe_episode(url: str) -> Dict[str, Any]:
    """Turn an episode page into a video with one format per quality+host."""
    status, body, _ = http.fetch(url)
    if status >= 400:
        raise ToonWorldError(f"The episode page returned HTTP {status}.")

    data = _unwrap_props(body)
    metadata = data.get("metadata") or {}
    encodes = data.get("encodes") or []
    if not encodes:
        raise ToonWorldError("That episode has no downloadable encodes.")

    qualities: List[Dict[str, Any]] = []
    formats: List[Dict[str, Any]] = []
    quality_map: Dict[str, Dict[str, Any]] = {}

    for encode in encodes:
        slug = _quality_slug(encode)
        label = (encode.get("readable") or {}).get("codec") or encode.get("resolution") or slug
        size = (encode.get("readable") or {}).get("size") or encode.get("size")
        height = encode.get("resolution")
        height_value = int(re.sub(r"[^0-9]", "", str(height)) or 0) if height else None

        sources = []
        for file_entry in encode.get("files") or []:
            host = file_entry.get("host") or "unknown"
            link = file_entry.get("link") or ""
            token = link.split("/redirect/")[-1] if "/redirect/" in link else ""
            # A site may also store a plain, direct file URL.  Those are used
            # as-is; only encrypted redirect tokens need decoding.
            direct = link if link.startswith("http") and "/redirect/" not in link else None
            source = {
                "host": host,
                "host_slug": _host_slug(host),
                "token": token,
                "direct": direct,
                "url": None,
                "status": "unknown",
                "error": None,
            }
            sources.append(source)
            formats.append(
                {
                    "format_id": f"{slug}-{_host_slug(host)}",
                    "ext": "mp4",
                    "height": height_value,
                    "fps": None,
                    "vcodec": encode.get("codec") or "unknown",
                    "acodec": "unknown",
                    "filesize": encode.get("size"),
                    "tbr": None,
                    "protocol": "https",
                    "format_note": f"{label} - {host}",
                    "dynamic_range": None,
                    "language": None,
                    "container": "mp4",
                    "host": host,
                    "quality_id": slug,
                    "token": token,
                }
            )

        quality = {
            "id": slug,
            "label": f"{label}" + (f" - {size}" if size else ""),
            "hint": f"{len(sources)} host(s): " + ", ".join(s["host"] for s in sources),
            "selector": f"toonworld:{slug}",
            "kind": "merged",
            "height": height_value,
            "hdr": bool(encode.get("is_hq")),
            "size": encode.get("size"),
            "requires_merge": False,
            "sources": sources,
        }
        qualities.append(quality)
        quality_map[slug] = quality

    title = metadata.get("name") or "Episode"
    show = metadata.get("show") or ""
    full_title = f"{show} - S{metadata.get('season')}E{metadata.get('episode')} - {title}" if show else title

    return {
        "kind": "video",
        "id": metadata.get("slug") or url.rsplit("/", 1)[-1],
        "title": full_title,
        "uploader": (metadata.get("encoder") or {}).get("name"),
        "thumbnail": metadata.get("poster"),
        "duration": (metadata.get("runtime") or 0) * 60 or None,
        "is_live": False,
        "webpage_url": url,
        "extractor": "ToonWorld4All",
        "ext": "mp4",
        "filesize": encodes[0].get("size") if encodes else None,
        "formats": formats,
        "qualities": qualities,
        "playlist_count": 0,
        "episode_number": metadata.get("episode"),
        "season_number": metadata.get("season"),
        "air_date": metadata.get("air"),
        "overview": metadata.get("overview"),
        "quality_map": quality_map,
    }


def probe(url: str) -> Dict[str, Any]:
    """Probe any toonworld4all.me URL (season page or episode page)."""
    if re.search(rf"{re.escape(ARCHIVE)}/episode/", url, re.I):
        analysis = probe_episode(url)
        analysis.pop("quality_map", None)
        return analysis
    return probe_season(url)


# --------------------------------------------------------------------------- #
# link resolution
# --------------------------------------------------------------------------- #


def resolve_destination(token: str, timeout: int = 60) -> Tuple[Optional[str], Optional[str]]:
    """Decode a redirect token into the real file URL.

    The archive app returns the destination page to a visitor that has no
    session cookie yet; that page prints the destination URL.  A second
    request with the same session is redirected straight to the ad shortener,
    so every token is resolved with a fresh session.
    """
    if not token:
        return None, "The link did not contain a redirect token."
    url = f"https://{ARCHIVE}/redirect/{token}"
    try:
        status, body, _ = http.fetch(url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return None, f"Could not reach the redirect service: {exc}"

    if status >= 400:
        return None, f"The redirect service returned HTTP {status}."

    for match in _CODE_RE.finditer(body):
        candidate = _TAG_RE.sub("", match.group(1)).strip()
        if candidate.startswith("http"):
            return candidate, None

    return None, "The redirect service did not reveal a destination URL."


_HEX24_RE = re.compile(r"^[0-9a-f]{24}$", re.I)


def validate_host_id(host: str, file_id: str) -> Optional[str]:
    """Return a problem description when a host id cannot possibly work.

    Each host expects a different identifier format.  When the archive app
    stores an id in an older format the host rejects it outright, and it is
    much more useful to say so than to report a generic HTTP error.
    """
    if not file_id:
        return "the link carries no file id"
    if host.lower() == "filepress" and not _HEX24_RE.match(file_id):
        return (
            f"invalid FilePress id '{file_id[:28]}' - FilePress only accepts a "
            "24 character hexadecimal object id"
        )
    if host.lower() == "appdrive" and len(file_id) < 6:
        return f"invalid AppDrive id '{file_id[:28]}' - AppDrive ids are at least 6 characters"
    if host.lower() == "mega" and len(file_id) != 22:
        return (
            f"invalid MEGA handle '{file_id[:28]}' ({len(file_id)} chars) - "
            "MEGA file handles are 22 characters"
        )
    return None


def probe_destination(url: str, timeout: int = 45) -> Dict[str, Any]:
    """Check whether a resolved destination actually serves a file.

    Returns a dict with ``state`` (one of ``ready``, ``missing``, ``blocked``,
    ``page`` or ``unknown``) and a human readable ``detail``.
    """
    if not url:
        return {"state": "unknown", "detail": "no destination URL"}

    try:
        status, body, headers = http.fetch(url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return {"state": "unknown", "detail": f"could not reach the host: {exc}"}

    content_type = (headers.get("content-type") or "").lower()
    lowered = body.lower() if isinstance(body, str) else ""

    # Several hosts answer "404 ! File Not Found" with an HTTP 200.
    if "file not found" in lowered[:400] or "no longer available" in lowered[:2000]:
        return {"state": "missing", "detail": "the host says the file does not exist"}
    if "invalid file id" in lowered[:2000] or "invaild file id" in lowered[:2000]:
        return {"state": "missing", "detail": "the host rejected the file id format"}

    if status == 404:
        return {"state": "missing", "detail": "HTTP 404 - the file does not exist on this host"}
    if status == 403:
        if "just a moment" in lowered or "cloudflare" in lowered:
            return {"state": "blocked", "detail": "blocked by a Cloudflare challenge"}
        return {"state": "blocked", "detail": "HTTP 403 - the host refused the request"}
    if status in (301, 302, 303, 307, 308):
        target = headers.get("location") or ""
        return {"state": "page", "detail": f"redirects to {target[:80]}"}
    if status >= 400:
        return {"state": "unknown", "detail": f"HTTP {status}"}

    if content_type.startswith("video/") or "octet-stream" in content_type:
        return {"state": "ready", "detail": f"serves {content_type.split(';')[0]}"}
    if "text/html" in content_type:
        if "page not found" in lowered:
            return {"state": "missing", "detail": "the host says the file was removed"}
        return {"state": "page", "detail": "returned a web page instead of a file"}
    return {"state": "unknown", "detail": f"HTTP {status} ({content_type or 'unknown type'})"}


def resolve_appdrive(file_id: str, timeout: int = 45) -> Tuple[Optional[str], Optional[str]]:
    """Resolve an AppDrive file id into its final download URL.

    AppDrive answers with a small HTML page that performs a JavaScript
    redirect carrying a signed token; the chain is followed here.
    """
    if not file_id:
        return None, "the link carries no AppDrive file id"
    url = f"https://appdrive.life/api/download/{file_id}"
    referer = f"https://appdrive.life/file/{file_id}"
    for _ in range(5):
        try:
            status, body, headers = http.fetch(url, referer=referer, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            return None, f"could not reach AppDrive: {exc}"
        if status >= 400:
            return None, f"AppDrive returned HTTP {status}"
        match = re.search(r"window\.location\.replace\('([^']+)'\)", body)
        if match:
            url = match.group(1)
            continue
        direct = re.search(
            r'"(https?://[^"]+\.(?:mp4|mkv|webm|avi|m3u8|zip)[^"]*)"', body, re.I
        ) or re.search(
            r'(?:finalUrl|final_url|downloadUrl|download_url)\s*[:=]\s*["\'](https?://[^"\']+)',
            body,
        )
        if direct:
            return direct.group(1), None
        return None, "AppDrive returned an advertisement page instead of a file link"
    return None, "AppDrive redirect chain did not terminate"


def resolve_sources(url: str, quality_id: str) -> List[Dict[str, Any]]:
    """Return the candidate file URLs for one quality, best host first."""
    analysis = probe_episode(url)
    quality = None
    for candidate in analysis.get("qualities", []):
        if candidate.get("id") == quality_id:
            quality = candidate
            break
    if quality is None:
        quality = (analysis.get("qualities") or [{}])[0]

    resolved: List[Dict[str, Any]] = []
    for source in quality.get("sources", []):
        token = source.get("token") or ""
        host = source.get("host") or ""
        if source.get("direct"):
            destination, error = source["direct"], None
        elif host.lower() == "appdrive" and token:
            destination, error = resolve_appdrive(token.rsplit("/", 1)[-1])
        else:
            destination, error = resolve_destination(token)
        status = {"state": "unknown", "detail": error or ""}
        if destination:
            status = probe_destination(destination)
            # The identifier the host actually receives is the last path
            # segment of the destination, not the redirect token.
            file_id = destination.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
            problem = validate_host_id(source.get("host") or "", file_id)
            if problem and status.get("state") in {"missing", "blocked", "unknown", "page"}:
                status = {"state": "missing", "detail": problem}
        resolved.append(
            {
                "host": source.get("host"),
                "token": token,
                "url": destination,
                "error": error,
                "state": status.get("state"),
                "detail": status.get("detail"),
            }
        )
    return resolved
