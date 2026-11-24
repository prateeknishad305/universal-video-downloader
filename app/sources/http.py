"""HTTP helpers with browser impersonation.

Many sites sit behind Cloudflare's bot detection, which rejects requests that
do not look like a real browser at the TLS/HTTP level.  ``curl_cffi`` lets us
present a genuine Chrome fingerprint, which clears the challenge without
needing a headless browser.

If ``curl_cffi`` is not installed the helpers fall back to plain
``urllib``, which still works for sites that do not fingerprint visitors.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

try:  # pragma: no cover - availability depends on the environment
    from curl_cffi import requests as _curl_requests

    HAS_CURL_CFFI = True
except Exception:  # pragma: no cover
    _curl_requests = None
    HAS_CURL_CFFI = False

import urllib.error
import urllib.request


def impersonation_available() -> bool:
    """True when browser impersonation can be used."""
    return HAS_CURL_CFFI


def fetch(
    url: str,
    *,
    referer: Optional[str] = None,
    timeout: int = 60,
    impersonate: Optional[str] = "chrome",
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, str, Dict[str, str]]:
    """GET *url* and return ``(status, body, headers)``."""
    request_headers = {
        "User-Agent": DEFAULT_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if referer:
        request_headers["Referer"] = referer
    if headers:
        request_headers.update(headers)

    if HAS_CURL_CFFI:
        kwargs = {"headers": request_headers, "timeout": timeout}
        if impersonate:
            kwargs["impersonate"] = impersonate
        response = _curl_requests.get(url, **kwargs)
        return response.status_code, response.text, dict(response.headers)

    request = urllib.request.Request(url, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
            return response.status, body, dict(response.headers)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return exc.code, body, dict(exc.headers)
