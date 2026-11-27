"""Resolvers that turn a host file id into a direct download URL.

Every host used by the toonworld archive exposes its files differently:

* **Filepress** serves a JSON description of a file; the description carries
  the download URL.
* **GDFlix** renders a server side page that embeds the Google Drive link.
* **HubCloud** protects its page with a small JavaScript challenge and then
  hands the download URL to ``api/downloader.php``.
* **MEGA** stores its files behind its own API and needs the file key.
* **AppDrive** answers with a page that redirects to the real file.

Each resolver reports what it found through :class:`HostResult` so the caller
can fail over to the next host instead of guessing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import http

FILEPRESS_BASE = "https://new4.filepress.lat"
GDFLIX_BASE = "https://new10.gdflix.io"
HUBCLOUD_BASE = "https://hubcloud.link"
MEGA_BASE = "https://mega.nz"

_MISSING_MARKERS = (
    "file not found",
    "page not found",
    "no longer available",
    "invaild file id",
    "invalid file id",
    "not available anymore",
)

_DIRECT_MEDIA_RE = re.compile(
    r"https?://[^\"'\s<>\\]+?\.(?:mkv|mp4|webm|avi|m3u8|mp3)(?:\?[^\"'\s<>\\]*)?",
    re.I,
)
_DRIVE_RE = re.compile(
    r"https?://(?:drive|docs)\.google\.com/[^\"'\s<>\\]+", re.I
)
_CHALLENGE_RE = re.compile(r"window\.location\.replace\('([^']+)'\)")


@dataclass
class HostResult:
    """The outcome of resolving one host."""

    url: Optional[str] = None
    state: str = "unknown"
    detail: str = ""
    file_name: Optional[str] = None
    kind: str = "http"
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        """True when a usable file URL was found."""
        return bool(self.url) and self.state == "ready"


def _looks_missing(body: str) -> bool:
    lowered = (body or "")[:4000].lower()
    return any(marker in lowered for marker in _MISSING_MARKERS)


def _first_url(values: List[str]) -> Optional[str]:
    for value in values:
        if value:
            return value
    return None


def _session():
    """Return a cookie aware HTTP session, or ``None`` when unavailable."""
    try:
        from curl_cffi import requests as curl_requests
    except Exception:
        return None
    return curl_requests.Session(impersonate="chrome")


def _session_get(session, url: str, *, referer: Optional[str] = None, timeout: int = 45):
    headers = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
    if referer:
        headers["Referer"] = referer
    response = session.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    return response.status_code, response.text, dict(response.headers)


def _session_post_json(session, url: str, payload: Dict[str, Any], *, timeout: int = 45):
    response = session.post(
        url,
        json=payload,
        headers={"Accept": "application/json, text/plain, */*"},
        timeout=timeout,
    )
    return response.status_code, response.text, dict(response.headers)


def _extract_url(payload: Any, depth: int = 0) -> Optional[str]:
    """Find a download URL inside an arbitrary JSON structure."""
    if depth > 6:
        return None
    preferred = ("downloadurl", "download_url", "directurl", "direct_url", "fileurl",
                 "file_url", "link", "url", "href", "location")
    if isinstance(payload, dict):
        lowered = {str(key).lower(): value for key, value in payload.items()}
        for key in preferred:
            value = lowered.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
        for value in payload.values():
            found = _extract_url(value, depth + 1)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _extract_url(value, depth + 1)
            if found:
                return found
    return None


# --------------------------------------------------------------------------- #
# host resolvers
# --------------------------------------------------------------------------- #


def resolve_filepress(file_id: str, *, referer: Optional[str] = None,
                      timeout: int = 45) -> HostResult:
    """Resolve a Filepress id through its public JSON endpoint."""
    if not file_id:
        return HostResult(state="missing", detail="the link carries no Filepress id")
    url = f"{FILEPRESS_BASE}/api/file/get/{file_id}"
    if referer:
        url = f"{url}?referrer={referer}"
    try:
        status, body, _ = http.fetch(url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return HostResult(state="unknown", detail=f"could not reach Filepress: {exc}")

    if status >= 400:
        return HostResult(state="unknown", detail=f"Filepress returned HTTP {status}")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        if _looks_missing(body):
            return HostResult(state="missing", detail="Filepress says the file does not exist")
        return HostResult(state="unknown", detail="Filepress returned an unreadable answer")

    if isinstance(payload, dict) and payload.get("status") is False:
        detail = (
            payload.get("statusText")
            or payload.get("error")
            or "Filepress does not know this file"
        )
        return HostResult(state="missing", detail=str(detail))

    direct = _extract_url(payload)
    if direct:
        return HostResult(url=direct, state="ready", detail="resolved through the Filepress API")

    name = None
    if isinstance(payload, dict):
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        name = data.get("fileName") or data.get("name") or data.get("title")
    return HostResult(
        state="page",
        detail="Filepress described the file but did not expose a download URL",
        file_name=name if isinstance(name, str) else None,
    )


def resolve_gdflix(file_id: str, *, timeout: int = 45) -> HostResult:
    """Resolve a GDFlix id into the Google Drive link it wraps."""
    if not file_id:
        return HostResult(state="missing", detail="the link carries no GDFlix id")
    url = f"{GDFLIX_BASE}/file/{file_id}"
    try:
        status, body, _ = http.fetch(url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return HostResult(state="unknown", detail=f"could not reach GDFlix: {exc}")

    if status >= 400:
        return HostResult(state="missing", detail=f"GDFlix returned HTTP {status}")
    if _looks_missing(body):
        return HostResult(state="missing", detail="GDFlix says the file does not exist")

    drive = _first_url(_DRIVE_RE.findall(body))
    if drive:
        return HostResult(url=drive, state="ready", detail="resolved to a Google Drive link")
    direct = _first_url(_DIRECT_MEDIA_RE.findall(body))
    if direct:
        return HostResult(url=direct, state="ready", detail="resolved to a direct media file")
    return HostResult(
        state="page",
        detail="GDFlix returned a page without a file link",
        url=url,
    )


def resolve_hubcloud(file_id: str, *, timeout: int = 45) -> HostResult:
    """Resolve a HubCloud id, passing its JavaScript challenge on the way."""
    if not file_id:
        return HostResult(state="missing", detail="the link carries no HubCloud id")
    page_url = f"{HUBCLOUD_BASE}/drive/{file_id}"
    session = _session()
    body = ""
    status = 0
    headers: Dict[str, str] = {}

    try:
        if session is not None:
            for _ in range(3):
                status, body, headers = _session_get(
                    session, page_url, referer=HUBCLOUD_BASE, timeout=timeout
                )
                challenge = _CHALLENGE_RE.search(body)
                if not challenge:
                    break
                page_url = challenge.group(1)
            try:
                status, body, headers = _session_get(
                    session, page_url, referer=HUBCLOUD_BASE, timeout=timeout
                )
            except Exception:
                pass
        else:
            status, body, headers = http.fetch(page_url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return HostResult(state="unknown", detail=f"could not reach HubCloud: {exc}")

    if status == 401 or "forbidden" in (body or "")[:200].lower():
        return HostResult(state="missing", detail="HubCloud says the file does not exist")
    if status >= 400:
        return HostResult(state="unknown", detail=f"HubCloud returned HTTP {status}")
    if _looks_missing(body):
        return HostResult(state="missing", detail="HubCloud says the file does not exist")

    api_result = _hubcloud_download_api(session, file_id, timeout=timeout)
    if api_result.ready:
        return api_result

    direct = _first_url(_DIRECT_MEDIA_RE.findall(body))
    if direct:
        return HostResult(url=direct, state="ready", detail="resolved to a direct media file")

    return HostResult(
        state=api_result.state,
        detail=api_result.detail or "HubCloud did not hand out a download URL",
        url=page_url,
    )


def _hubcloud_download_api(session, file_id: str, *, timeout: int = 45) -> HostResult:
    """Ask ``api/downloader.php`` for the real file URL."""
    api_url = f"{HUBCLOUD_BASE}/api/downloader.php"
    payload = {"id": file_id, "referer": f"{HUBCLOUD_BASE}/drive/{file_id}"}
    try:
        if session is not None:
            status, body, _ = _session_post_json(session, api_url, payload, timeout=timeout)
        else:
            status, body, _ = http.fetch(
                api_url,
                timeout=timeout,
                headers={"Content-Type": "application/json"},
            )
    except Exception as exc:  # noqa: BLE001
        return HostResult(state="unknown", detail=f"could not reach the HubCloud API: {exc}")

    if status >= 400:
        return HostResult(state="unknown", detail=f"the HubCloud API returned HTTP {status}")

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        direct = _first_url(_DIRECT_MEDIA_RE.findall(body))
        if direct:
            return HostResult(url=direct, state="ready", detail="resolved through the HubCloud API")
        return HostResult(state="page", detail="the HubCloud API returned an advertisement")

    if isinstance(data, dict):
        link = data.get("link") or data.get("url") or data.get("downloadLink")
        if isinstance(link, str) and link.startswith("http"):
            return HostResult(url=link, state="ready", detail="resolved through the HubCloud API")
        if data.get("success") is False or data.get("status") is False:
            message = data.get("message") or data.get("error") or "HubCloud does not know this file"
            return HostResult(state="missing", detail=str(message))
    return HostResult(state="page", detail="the HubCloud API did not return a file link")


def resolve_appdrive(file_id: str, *, timeout: int = 45) -> HostResult:
    """Resolve an AppDrive id by following its JavaScript redirect."""
    if not file_id:
        return HostResult(state="missing", detail="the link carries no AppDrive id")
    url = f"https://appdrive.life/api/download/{file_id}"
    referer = f"https://appdrive.life/file/{file_id}"
    for _ in range(5):
        try:
            status, body, _ = http.fetch(url, referer=referer, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            return HostResult(state="unknown", detail=f"could not reach AppDrive: {exc}")
        if status >= 400:
            return HostResult(state="unknown", detail=f"AppDrive returned HTTP {status}")
        match = re.search(r"window\.location\.replace\('([^']+)'\)", body)
        if match:
            url = match.group(1)
            continue
        direct = _first_url(_DIRECT_MEDIA_RE.findall(body)) or _extract_url(
            re.search(r"finalUrl\s*=\s*[\"']([^\"']+)", body).group(1)
            if re.search(r"finalUrl\s*=\s*[\"']([^\"']+)", body)
            else None
        )
        if direct:
            return HostResult(url=direct, state="ready", detail="resolved through AppDrive")
        return HostResult(state="page", detail="AppDrive returned an advertisement page")
    return HostResult(state="unknown", detail="the AppDrive redirect chain did not terminate")


def resolve_mega(file_id: str, *, timeout: int = 45) -> HostResult:
    """Resolve a MEGA handle and key into the API download URL."""
    handle, key = split_mega_id(file_id)
    if not handle or not key:
        return HostResult(
            state="missing",
            detail="the MEGA link does not contain a file key",
        )
    from . import mega

    try:
        resolved = mega.resolve_file(handle, key, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return HostResult(state="unknown", detail=f"could not reach MEGA: {exc}")
    if not resolved:
        return HostResult(state="missing", detail="MEGA does not know this file")
    size, download_url = resolved
    return HostResult(
        url=download_url,
        state="ready",
        detail="resolved through the MEGA API",
        kind="mega",
        extra={"handle": handle, "key": key, "size": size},
    )


def split_mega_id(file_id: str) -> Tuple[str, str]:
    """Split a MEGA identifier into its handle and its file key."""
    if not file_id:
        return "", ""
    value = file_id.split("?")[0].strip()
    if "#" in value:
        handle, key = value.split("#", 1)
        handle = handle.rstrip("/").rsplit("/", 1)[-1]
        return handle.strip(), key.strip()
    if "/" in value:
        parts = [part for part in value.split("/") if part]
        if len(parts) >= 2:
            return parts[-2], parts[-1]
    return value, ""


RESOLVERS = {
    "filepress": resolve_filepress,
    "gdflix": resolve_gdflix,
    "hubcloud": resolve_hubcloud,
    "appdrive": resolve_appdrive,
    "mega": resolve_mega,
}


def normalize_host(host: str) -> str:
    """Map a host label onto the key used by :data:`RESOLVERS`."""
    value = re.sub(r"[^a-z0-9]+", "", (host or "").lower())
    aliases = {
        "filepress": "filepress",
        "filebee": "filepress",
        "gdflix": "gdflix",
        "hubcloud": "hubcloud",
        "appdrive": "appdrive",
        "mega": "mega",
        "megadrive": "mega",
    }
    return aliases.get(value, value)


def resolve(host: str, file_id: str, *, referer: Optional[str] = None,
            timeout: int = 45) -> HostResult:
    """Resolve *file_id* on *host* into a downloadable URL."""
    key = normalize_host(host)
    resolver = RESOLVERS.get(key)
    if resolver is None:
        return HostResult(state="unknown", detail=f"no resolver for host '{host}'")
    if key == "filepress":
        return resolver(file_id, referer=referer, timeout=timeout)
    try:
        return resolver(file_id, timeout=timeout)
    except TypeError:
        return resolver(file_id)
