"""Native support for MultiMovies.

``multimovies.wtf`` is the public landing page; the actual catalogue lives on
``m1.multimovies.courses`` (and ``multinime.site`` for anime).  Both are
server-rendered apps that expose their player configuration as a
``watchConfig`` object embedded in the page:

``watchConfig.initialServers`` lists every streaming provider offered for the
episode, each with an embed URL.  Those providers hide the media URL behind
their own JavaScript, so the source module resolves them through
:mod:`app.sources.embed_resolver` and then reports the qualities that the
resolved HLS master playlist offers.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from . import http
from .embed_resolver import (
    extract_json_object,
    quality_tier,
    resolve_embed,
    resolve_manifest,
)

SITE = "multimovies.wtf"
CONTENT = "multimovies.courses"
ANIME = "multinime.site"

_EPISODE_PATH_RE = re.compile(r"/series/[^/]+/season/(\d+)/episode/(\d+)", re.I)
_SERIES_PATH_RE = re.compile(r"/series/[^/]+", re.I)
_MOVIE_PATH_RE = re.compile(r"/movie/[^/]+", re.I)
_EPISODE_LINK_RE = re.compile(r"(/series/[^/]+/season/\d+/episode/\d+)", re.I)
_MOVIE_LINK_RE = re.compile(r"(/movie/[a-z0-9\-]+)", re.I)


class MultiMoviesError(Exception):
    """Raised when a MultiMovies page cannot be parsed."""


def matches(url: str) -> bool:
    """True when *url* points at a page this source understands."""
    lowered = (url or "").lower()
    return any(host in lowered for host in (SITE, CONTENT, ANIME))


def _slug_from_path(url: str) -> str:
    path = re.sub(r"^https?://[^/]+", "", url).split("?")[0].rstrip("/")
    return path or "/"


def _host_of(url: str) -> str:
    match = re.match(r"https?://([^/]+)", url)
    return match.group(1) if match else ""


def _title_from_html(html: str) -> str:
    match = re.search(r"<title>(.*?)</title>", html, re.S)
    if not match:
        return "MultiMovies"
    title = match.group(1).strip()
    title = re.sub(r"&#039;", "'", title)
    title = re.sub(r"&amp;", "&", title)
    return title.split("|")[0].split("- Watch")[0].strip() or "MultiMovies"


def _json_ld(html: str) -> Dict[str, Any]:
    """Best-effort metadata from the schema.org block."""
    import json

    for match in re.finditer(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S
    ):
        try:
            payload = json.loads(match.group(1).strip())
        except Exception:
            continue
        entries = payload if isinstance(payload, list) else [payload]
        for entry in entries:
            if isinstance(entry, dict) and entry.get("@type") in {"TVEpisode", "Movie", "TVSeries"}:
                return entry
    return {}


def _poster(html: str) -> Optional[str]:
    match = re.search(r'"image"\s*:\s*"([^"]+)"', html)
    if match:
        return match.group(1)
    match = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    return match.group(1) if match else None


# --------------------------------------------------------------------------- #
# playlist pages
# --------------------------------------------------------------------------- #


def probe_series(url: str) -> Dict[str, Any]:
    """Turn a series page into a playlist of episodes."""
    status, html, _ = http.fetch(url, timeout=45)
    if status >= 400:
        raise MultiMoviesError(f"The series page returned HTTP {status}.")

    entries: List[Dict[str, Any]] = []
    seen = set()
    for match in _EPISODE_LINK_RE.finditer(html):
        path = match.group(1)
        if path in seen:
            continue
        seen.add(path)
        season = re.search(r"/season/(\d+)/", path)
        episode = re.search(r"/episode/(\d+)", path)
        entries.append(
            {
                "id": path.strip("/").replace("/", "-"),
                "title": (
                    f"Season {season.group(1)} Episode {episode.group(1)}"
                    if season and episode
                    else path
                ),
                "url": _absolute(url, path),
                "duration": None,
                "thumbnail": None,
                "uploader": None,
            }
        )

    if not entries:
        raise MultiMoviesError("No episodes were found on that series page.")

    meta = _json_ld(html)
    title = meta.get("name") or _title_from_html(html)
    return {
        "kind": "playlist",
        "id": meta.get("url") or url,
        "title": title,
        "uploader": "MultiMovies",
        "thumbnail": _poster(html),
        "webpage_url": url,
        "playlist_count": len(entries),
        "entries": entries,
        "extractor": "MultiMovies",
    }


def probe_movie_listing(url: str) -> Dict[str, Any]:
    """Turn the movie index into a playlist of movies."""
    status, html, _ = http.fetch(url, timeout=45)
    if status >= 400:
        raise MultiMoviesError(f"The listing returned HTTP {status}.")
    entries: List[Dict[str, Any]] = []
    seen = set()
    for match in _MOVIE_LINK_RE.finditer(html):
        path = match.group(1)
        if path in seen:
            continue
        seen.add(path)
        entries.append(
            {
                "id": path.strip("/").replace("/", "-"),
                "title": path.rsplit("/", 1)[-1].replace("-", " ").title(),
                "url": _absolute(url, path),
                "duration": None,
                "thumbnail": None,
                "uploader": None,
            }
        )
    if not entries:
        raise MultiMoviesError("No movies were found on that page.")
    return {
        "kind": "playlist",
        "id": url,
        "title": _title_from_html(html),
        "uploader": "MultiMovies",
        "thumbnail": None,
        "webpage_url": url,
        "playlist_count": len(entries),
        "entries": entries,
        "extractor": "MultiMovies",
    }


def _absolute(base: str, path: str) -> str:
    if path.startswith("http"):
        return path
    host = _host_of(base)
    return f"https://{host}{path}" if path.startswith("/") else f"https://{host}/{path}"


# --------------------------------------------------------------------------- #
# episode / movie pages
# --------------------------------------------------------------------------- #


def _collect_servers(html: str) -> List[Dict[str, Any]]:
    config = extract_json_object(html, "watchConfig") or {}
    servers = config.get("initialServers") or []
    return [server for server in servers if server.get("url")]


def _server_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "server").lower()).strip("-")


def probe_episode(url: str, resolve: bool = True) -> Dict[str, Any]:
    """Turn an episode or movie page into a video with per-server qualities."""
    status, html, _ = http.fetch(url, timeout=45)
    if status >= 400:
        raise MultiMoviesError(f"The page returned HTTP {status}.")

    servers = _collect_servers(html)
    if not servers:
        raise MultiMoviesError("That page does not expose any streaming servers.")

    formats: List[Dict[str, Any]] = []
    server_report: List[Dict[str, Any]] = []
    sources_by_quality: Dict[str, List[Dict[str, Any]]] = {}

    for server in servers:
        name = server.get("name") or "server"
        embed_url = server["url"]
        resolved = resolve_embed(embed_url, allow_browser=False)
        media = resolved.get("media") or []
        if not media:
            server_report.append(
                {
                    "name": name,
                    "state": "unresolved",
                    "detail": (resolved.get("error") or "no media found")[:160],
                }
            )
            continue

        master = media[0]
        variants: List[Dict[str, Any]] = []
        try:
            variants = resolve_manifest(master, referer=embed_url)
        except Exception as exc:  # noqa: BLE001
            server_report.append({"name": name, "state": "unresolved", "detail": str(exc)[:160]})
            continue

        heights = []
        for variant in variants:
            height = variant.get("height")
            tier = quality_tier(height)
            quality_id = f"{tier}p" if tier else "auto"
            media_url = variant.get("url") or master
            formats.append(
                {
                    "format_id": f"{_server_slug(name)}-{quality_id}",
                    "ext": "mp4",
                    "height": height,
                    "fps": None,
                    "vcodec": "unknown",
                    "acodec": "unknown",
                    "filesize": None,
                    "tbr": variant.get("bandwidth"),
                    "protocol": "m3u8",
                    "format_note": f"{name} - {quality_id}",
                    "dynamic_range": None,
                    "language": None,
                    "container": "mp4",
                    "server": name,
                    "quality_id": quality_id,
                    "embed_url": embed_url,
                    "media_url": media_url,
                }
            )
            if height:
                heights.append(height)
            sources_by_quality.setdefault(quality_id, [])
            sources_by_quality[quality_id].append(
                {"name": name, "embed_url": embed_url, "method": resolved.get("method")}
            )
        server_report.append(
            {
                "name": name,
                "state": "ready",
                "detail": f"{len(variants)} qualities up to {max(heights) if heights else '?'}p",
            }
        )

    if not formats:
        raise MultiMoviesError("None of the streaming servers could be resolved.")

    qualities: List[Dict[str, Any]] = []
    for quality_id, servers_for_quality in sources_by_quality.items():
        height = int(quality_id.rstrip("p")) if quality_id.endswith("p") else None
        qualities.append(
            {
                "id": quality_id,
                "label": f"{quality_id} - {len(servers_for_quality)} server(s)",
                "hint": ", ".join(source["name"] for source in servers_for_quality),
                "selector": f"multimovies:{quality_id}",
                "kind": "merged",
                "height": height,
                "hdr": False,
                "size": None,
                "requires_merge": False,
                "servers": servers_for_quality,
            }
        )
    qualities.sort(key=lambda item: item["height"] or 0, reverse=True)
    # Merge tiers that describe the same picture size.
    merged: Dict[str, Dict[str, Any]] = {}
    for quality in qualities:
        key = quality["id"]
        if key in merged:
            merged[key]["servers"].extend(quality["servers"])
            merged[key]["label"] = f"{key} - {len(merged[key]['servers'])} server(s)"
            merged[key]["hint"] = ", ".join(s["name"] for s in merged[key]["servers"])
        else:
            merged[key] = quality
    qualities = sorted(merged.values(), key=lambda item: item["height"] or 0, reverse=True)

    meta = _json_ld(html)
    title = meta.get("name") or _title_from_html(html)
    return {
        "kind": "video",
        "id": meta.get("url") or url,
        "title": title,
        "uploader": "MultiMovies",
        "thumbnail": _poster(html),
        "duration": None,
        "is_live": False,
        "webpage_url": url,
        "extractor": "MultiMovies",
        "ext": "mp4",
        "filesize": None,
        "formats": formats,
        "qualities": qualities,
        "playlist_count": 0,
        "servers": server_report,
        "sources_by_quality": sources_by_quality,
    }


def probe(url: str) -> Dict[str, Any]:
    """Probe any MultiMovies URL."""
    path = _slug_from_path(url)
    if _EPISODE_PATH_RE.search(path) or _MOVIE_PATH_RE.search(path):
        return probe_episode(url)
    if _SERIES_PATH_RE.search(path):
        return probe_series(url)
    if path in {"/movies", "/movie", "/series"} or path.endswith("list"):
        return probe_movie_listing(url)
    # Fall back: a listing page with links.
    try:
        return probe_movie_listing(url)
    except MultiMoviesError:
        return probe_series(url)


def resolve_sources(url: str, quality_id: str) -> List[Dict[str, Any]]:
    """Resolve the servers that offer *quality_id* into playable URLs.

    The links are resolved again at download time because the providers hand
    out short-lived tokens.
    """
    analysis = probe_episode(url)
    sources = (analysis.get("sources_by_quality") or {}).get(quality_id) or []

    resolved: List[Dict[str, Any]] = []
    for source in sources:
        result = resolve_embed(source["embed_url"], allow_browser=True)
        media = result.get("media") or []
        if not media:
            resolved.append(
                {
                    "host": source["name"],
                    "url": None,
                    "referer": source["embed_url"],
                    "state": "unresolved",
                    "detail": (result.get("error") or "no media found")[:200],
                }
            )
            continue

        master = media[0]
        variants: List[Dict[str, Any]] = []
        try:
            variants = resolve_manifest(master, referer=source["embed_url"])
        except Exception:
            variants = []

        target = master
        for variant in variants:
            tier = quality_tier(variant.get("height"))
            if f"{tier}p" == quality_id:
                target = variant.get("url") or master
                break
        resolved.append(
            {
                "host": source["name"],
                "url": target,
                "referer": source["embed_url"],
                "state": "ready",
                "detail": f"resolved via {result.get('method')}",
            }
        )
    return resolved
