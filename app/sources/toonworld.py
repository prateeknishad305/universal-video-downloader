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

import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from . import http
from . import toonworld_hosts

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


# --------------------------------------------------------------------------- #
# interstitial page
# --------------------------------------------------------------------------- #

_HOST_PATTERNS = (
    ("hubcloud", re.compile(r"hubcloud\.[a-z.]+/drive/([A-Za-z0-9]{6,32})", re.I)),
    ("filepress", re.compile(r"filepress\.[a-z.]+/file/([A-Za-z0-9]{16,32})", re.I)),
    ("gdflix", re.compile(r"gdflix\.[a-z.]+/file/([A-Za-z0-9]{6,32})", re.I)),
    ("mega", re.compile(r"mega\.nz/file/([A-Za-z0-9]{8})#([A-Za-z0-9_-]{20,64})", re.I)),
    ("appdrive", re.compile(r"appdrive\.[a-z.]+/file/([A-Za-z0-9]{6,32})", re.I)),
)


def host_id_from_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    """Map a host URL onto ``(host, file id)``."""
    for host, pattern in _HOST_PATTERNS:
        match = pattern.search(url or "")
        if match:
            if host == "mega":
                return host, f"{match.group(1)}#{match.group(2)}"
            return host, match.group(1)
    return None, None


def resolve_interstitial(token: str, timeout: int = 60) -> Dict[str, Any]:
    """Read the interstitial page of a redirect token.

    The page carries the ad shortener that guards the real links as well as
    the host the token belongs to.  Both are returned so the caller can decide
    how to continue.
    """
    if not token:
        return {"error": "The link did not contain a redirect token."}
    url = f"https://{ARCHIVE}/redirect/{token}"
    try:
        status, body, _ = http.fetch(url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Could not reach the redirect service: {exc}"}

    if status >= 400:
        return {"error": f"The redirect service returned HTTP {status}."}

    props: Dict[str, Any] = {}
    match = _PROPS_RE.search(body)
    if match:
        raw = match.group(1).replace('\\"', '"').replace("\\'", "'")
        try:
            props = json.loads(raw)
        except json.JSONDecodeError:
            props = {}

    link = props.get("link") or {}
    host = link.get("domain") or None
    file_id = link.get("hidden") or None
    shortener = props.get("destination") or None

    printed = None
    for code_match in _CODE_RE.finditer(body):
        candidate = _TAG_RE.sub("", code_match.group(1)).strip()
        if candidate.startswith("http"):
            printed = candidate
            break

    return {
        "destination": printed or shortener,
        "shortener": shortener,
        "host": host,
        "file_id": file_id,
        "props": props,
        "error": None,
    }


def resolve_via_browser(token: str, timeout: int = 120) -> Dict[str, Any]:
    """Open the interstitial in a real browser and follow it to the links.

    The archive sends the visitor through an ad shortener.  A browser runs the
    shortener's own JavaScript, which is the only way to learn where the real
    files live.  Nothing is clicked inside a verification widget: the page is
    given the chance to verify itself.
    """
    from . import browser as browser_module

    if not token:
        return {"error": "The link did not contain a redirect token."}
    if not browser_module.browser_available():
        return {"error": "No browser is available to follow the download page."}

    session = browser_module.BrowserSession(timeout_ms=timeout * 1000)
    try:
        session.start()
    except browser_module.BrowserUnavailable as exc:
        return {"error": str(exc)}

    try:
        outcome = _follow_token_in_session(session, token, timeout)
        return outcome
    finally:
        session.close()


def _follow_token_in_session(session, token: str, timeout: int) -> Dict[str, Any]:
    """Follow one interstitial inside an open browser session."""
    interstitial = f"https://{ARCHIVE}/redirect/{token}"
    session.isolated_context(domain=ARCHIVE)
    page = session.page
    try:
        page.goto(interstitial, wait_until="domcontentloaded", timeout=timeout * 1000)
        page.wait_for_timeout(1500)
        try:
            props = page.evaluate("() => window.__PROPS__ || null")
        except Exception:
            props = None
        destination = (props or {}).get("destination") if isinstance(props, dict) else None
        if not destination:
            destination = interstitial

        page.goto(destination, wait_until="domcontentloaded", timeout=timeout * 1000)
        deadline = time.monotonic() + timeout
        links: List[Dict[str, Any]] = []
        while time.monotonic() < deadline:
            links = _page_host_links(page)
            if links:
                break
            if not _same_host(page.url, destination):
                host, file_id = host_id_from_url(page.url)
                if host and file_id:
                    links = [{"host": host, "file_id": file_id, "url": page.url}]
                    break
            page.wait_for_timeout(2000)
        if links:
            return {"links": links, "error": None, "final_url": page.url}
        return {
            "links": [],
            "error": _shortener_problem(page),
            "final_url": page.url,
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"The browser could not follow the download page: {exc}"}


def _shortener_problem(page) -> str:
    """Describe why the ad shortener did not hand out a file link."""
    try:
        text = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
    except Exception:
        text = ""
    try:
        frames = page.evaluate(
            "() => Array.from(document.querySelectorAll('iframe')).map(f => f.src).filter(Boolean)"
        )
    except Exception:
        frames = []
    lowered = (text or "").lower()
    if any("challenges.cloudflare.com" in str(frame) for frame in frames or []) or (
        "verify" in lowered or "verification" in lowered
    ):
        return (
            "the ad shortener asks for a Cloudflare verification that did not "
            "complete on its own"
        )
    if "just a moment" in lowered or "checking your browser" in lowered:
        return "the ad shortener is still checking the browser"
    return "the ad shortener did not offer a file link"


def _page_host_links(page) -> List[Dict[str, Any]]:
    """Collect the host links a page offers."""
    try:
        found = page.evaluate(
            "() => Array.from(document.querySelectorAll('a[href]')).map(a => a.href)"
        )
    except Exception:
        return []
    links: List[Dict[str, Any]] = []
    for link in found or []:
        if not isinstance(link, str) or not link.startswith("http"):
            continue
        host, file_id = host_id_from_url(link)
        if host and file_id:
            links.append({"host": host, "file_id": file_id, "url": link})
    return links


def _same_host(first: str, second: str) -> bool:
    def netloc(url: str) -> str:
        match = re.match(r"https?://([^/]+)", url or "", re.I)
        return match.group(1).lower() if match else ""

    return netloc(first) == netloc(second)



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
    result = toonworld_hosts.resolve_appdrive(file_id, timeout=timeout)
    if result.ready:
        return result.url, None
    return None, result.detail or "AppDrive did not return a file link"


def resolve_sources(url: str, quality_id: str) -> List[Dict[str, Any]]:
    """Return the candidate file URLs for one quality, best host first.

    Every source is resolved through its host resolver.  The archive hands out
    a shortener protected link per host; when the ids it exposes turn out to
    be placeholders, a browser session is used to follow the shortener and to
    pick up the real links.
    """
    analysis = probe_episode(url)
    quality = None
    for candidate in analysis.get("qualities", []):
        if candidate.get("id") == quality_id:
            quality = candidate
            break
    if quality is None:
        quality = (analysis.get("qualities") or [{}])[0]

    expected_size = quality.get("size")
    if not isinstance(expected_size, int):
        expected_size = None

    resolved: List[Dict[str, Any]] = []
    browser_links: Optional[Dict[str, List[Dict[str, Any]]]] = None

    for source in quality.get("sources", []):
        token = source.get("token") or ""
        host = source.get("host") or ""
        detail = ""
        destination: Optional[str] = None
        state = "unknown"
        kind = "http"
        extra: Dict[str, Any] = {}
        host_known = False

        if source.get("direct"):
            destination = source["direct"]
            state = "ready"
            detail = "direct link from the archive"
        elif token:
            interstitial = resolve_interstitial(token)
            if interstitial.get("error"):
                detail = interstitial["error"]
            else:
                file_id = interstitial.get("file_id")
                host_name = host or interstitial.get("host") or ""
                if file_id:
                    host_known = True
                    result = toonworld_hosts.resolve(
                        host_name, file_id, referer=url
                    )
                    destination = result.url
                    state = result.state
                    detail = result.detail
                    kind = result.kind
                    extra = result.extra
                else:
                    detail = "the download page did not expose a file id"

        if not destination and host_known:
            if browser_links is None:
                browser_links = _browser_links_for_sources(quality, url)
            entry = browser_links.get(token) or {}
            links = entry.get("links") or []
            if links:
                replacement = _match_browser_link(links, host, token)
                if replacement:
                    result = toonworld_hosts.resolve(
                        replacement["host"],
                        replacement["file_id"],
                        referer=url,
                    )
                    destination = result.url
                    state = result.state
                    detail = result.detail
                    kind = result.kind
                    extra = result.extra
            elif entry.get("error") and detail:
                detail = f"{detail}; {entry['error']}"
            elif entry.get("error"):
                detail = entry["error"]

        if destination and state == "ready" and kind != "mega":
            status = probe_destination(destination)
            if status.get("state") == "missing":
                state = "missing"
                detail = status.get("detail") or detail
            elif status.get("state") == "blocked":
                state = "blocked"
                detail = status.get("detail") or detail

        resolved.append(
            {
                "host": host,
                "token": token,
                "url": destination,
                "error": None if destination else (detail or "no destination"),
                "state": state,
                "detail": detail,
                "kind": kind,
                "extra": extra,
                "expected_size": expected_size,
            }
        )
    return resolved


def _browser_links_for_sources(
    quality: Dict[str, Any], url: str, timeout: int = 150
) -> Dict[str, Dict[str, Any]]:
    """Follow every interstitial in one browser session.

    The archive sends one shortener link per host, so every token is opened in
    the same browser.  The result maps a token onto the links the shortener
    finally offered.  The answers are cached, because the shortener hands out
    the same links for a while.
    """
    from . import browser as browser_module

    tokens = [source.get("token") for source in quality.get("sources", []) if source.get("token")]
    if not tokens:
        return {}

    collected: Dict[str, Dict[str, Any]] = {}
    pending = []
    for token in tokens:
        cached = _cached_browser_links(token)
        if cached is None:
            pending.append(token)
        else:
            collected[token] = cached
    if not pending:
        return collected
    if not browser_module.browser_available():
        return collected

    budget = max(30, timeout // max(1, len(pending)))
    session = browser_module.BrowserSession(timeout_ms=budget * 1000)
    try:
        session.start()
    except browser_module.BrowserUnavailable:
        return collected

    try:
        for token in pending:
            outcome = _follow_token_in_session(session, token, budget)
            entry = {"links": outcome.get("links") or [], "error": outcome.get("error")}
            collected[token] = entry
            _cache_browser_links(token, entry)
    finally:
        session.close()
    return collected


_BROWSER_LINK_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_BROWSER_LINK_CACHE_TTL = 1800.0


def _cached_browser_links(token: str) -> Optional[Dict[str, Any]]:
    """Return the links of a token when they are still fresh."""
    entry = _BROWSER_LINK_CACHE.get(token)
    if not entry:
        return None
    stored_at, links = entry
    if time.monotonic() - stored_at > _BROWSER_LINK_CACHE_TTL:
        _BROWSER_LINK_CACHE.pop(token, None)
        return None
    return links


def _cache_browser_links(token: str, links: Dict[str, Any]) -> None:
    _BROWSER_LINK_CACHE[token] = (time.monotonic(), dict(links))


def _match_browser_link(
    links: List[Dict[str, Any]], host: str, token: str
) -> Optional[Dict[str, Any]]:
    """Pick the browser link that belongs to *host*."""
    wanted = toonworld_hosts.normalize_host(host)
    for link in links:
        if toonworld_hosts.normalize_host(link.get("host") or "") == wanted:
            return link
    return links[0] if len(links) == 1 else None

