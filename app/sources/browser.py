"""Browser automation for links that are gated behind a JavaScript challenge.

Some hosts (and the ad shortener used by the toonworld archive) answer a
plain HTTP request with a small script that immediately navigates somewhere
else.  Replaying that script by hand is brittle, so a real Chromium session is
used instead: it executes the challenge, keeps the cookies it hands out and
reports where the visitor finally lands.

The browser is optional.  When Playwright or Chromium is missing the helpers
raise :class:`BrowserUnavailable` so callers can fall back to plain HTTP.

The synchronous Playwright API may only be started once per thread, so every
thread keeps its own driver and the sessions share a lock.  The profile is
kept on disk, which preserves the clearance cookie between runs.
"""

from __future__ import annotations

import atexit
import os
import shutil
import threading
import time
from typing import Any, Dict, List, Optional

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/156.0.0.0 Safari/537.36"
)

CHROMIUM_CANDIDATES = (
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
)

_BROWSER_LOCK = threading.Lock()
_THREAD_STATE = threading.local()
_DRIVERS: List[Any] = []


class BrowserUnavailable(Exception):
    """Raised when no usable browser is installed."""


def chromium_path() -> Optional[str]:
    """Return the path of a Chromium build, if one can be found."""
    configured = (os.environ.get("UVD_BROWSER_EXECUTABLE") or "").strip()
    if configured:
        return configured if os.path.exists(configured) else None
    for candidate in CHROMIUM_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which(
        "google-chrome"
    )


def playwright_available() -> bool:
    """True when the Playwright package can be imported."""
    try:
        import playwright.sync_api  # noqa: F401
    except Exception:
        return False
    return True


def browser_available() -> bool:
    """True when both Playwright and a Chromium build are present."""
    return playwright_available() and chromium_path() is not None


def _stop_drivers() -> None:
    for driver in list(_DRIVERS):
        try:
            driver.stop()
        except Exception:
            pass
    _DRIVERS.clear()


atexit.register(_stop_drivers)


def _driver_for_thread():
    """Return the Playwright driver of the current thread, starting it once."""
    driver = getattr(_THREAD_STATE, "driver", None)
    if driver is not None:
        return driver
    from playwright.sync_api import sync_playwright

    driver = sync_playwright().start()
    _THREAD_STATE.driver = driver
    _DRIVERS.append(driver)
    return driver


class BrowserSession:
    """A Chromium session with a persistent profile.

    The profile keeps cookies between runs, which matters for the clearance
    cookie a Cloudflare challenge hands out.
    """

    def __init__(
        self,
        *,
        headless: Optional[bool] = None,
        timeout_ms: int = 45000,
        user_data_dir: Optional[str] = None,
        user_agent: str = DEFAULT_USER_AGENT,
        viewport: Optional[Dict[str, int]] = None,
    ) -> None:
        from .. import config

        self.headless = config.BROWSER_HEADLESS if headless is None else headless
        self.timeout_ms = timeout_ms
        self.user_data_dir = user_data_dir or str(config.BROWSER_PROFILE_DIR)
        self.user_agent = user_agent
        self.viewport = viewport or {"width": 1366, "height": 768}
        self._driver = None
        self._context = None
        self._page = None
        self._owns_lock = False

    def __enter__(self) -> "BrowserSession":
        self.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def start(self) -> "BrowserSession":
        """Launch the browser and open a page."""
        if not playwright_available():
            raise BrowserUnavailable("Playwright is not installed.")
        executable = chromium_path()
        if not executable:
            raise BrowserUnavailable("No Chromium build was found on this machine.")

        os.makedirs(self.user_data_dir, exist_ok=True)
        _BROWSER_LOCK.acquire()
        self._owns_lock = True
        try:
            self._driver = _driver_for_thread()
            self._context = self._driver.chromium.launch_persistent_context(
                self.user_data_dir,
                executable_path=executable,
                headless=self.headless,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
                user_agent=self.user_agent,
                viewport=self.viewport,
                locale="en-US",
                ignore_https_errors=True,
            )
        except Exception:
            self._release()
            raise
        self._context.set_default_timeout(self.timeout_ms)
        pages = self._context.pages
        self._page = pages[0] if pages else self._context.new_page()
        return self

    def close(self) -> None:
        """Close the browser page and release the shared lock."""
        try:
            if self._context is not None:
                self._context.close()
        except Exception:
            pass
        self._page = None
        self._context = None
        self._release()

    def _release(self) -> None:
        if self._owns_lock:
            self._owns_lock = False
            try:
                _BROWSER_LOCK.release()
            except RuntimeError:
                pass

    @property
    def page(self):
        """The active page of the session."""
        if self._page is None:
            raise BrowserUnavailable("The browser session is not running.")
        return self._page

    @property
    def context(self):
        """The browser context of the session."""
        if self._context is None:
            raise BrowserUnavailable("The browser session is not running.")
        return self._context

    def isolated_context(self, domain: Optional[str] = None):
        """Return a context whose cookies of *domain* were dropped.

        The archive hands out its interstitial only to a visitor without a
        session cookie, while the cookies of the ad shortener have to stay:
        they hold the clearance a challenge handed out.
        """
        if self._context is None:
            raise BrowserUnavailable("The browser session is not running.")
        if domain:
            try:
                self._context.clear_cookies(domain=domain)
            except Exception:
                self.clear_cookies()
        else:
            self.clear_cookies()
        return self._context

    def new_page(self):
        """Open an additional page in the same session."""
        if self._context is None:
            raise BrowserUnavailable("The browser session is not running.")
        return self._context.new_page()

    def clear_cookies(self) -> None:
        """Drop every cookie, used when a token must be opened in isolation."""
        if self._context is not None:
            try:
                self._context.clear_cookies()
            except Exception:
                pass

    def goto(self, url: str, *, wait_until: str = "domcontentloaded") -> Any:
        """Navigate to *url* and return the response."""
        return self.page.goto(url, wait_until=wait_until, timeout=self.timeout_ms)

    def wait_for_timeout(self, milliseconds: int) -> None:
        """Wait for the given number of milliseconds."""
        self.page.wait_for_timeout(milliseconds)

    def page_text(self, limit: int = 2000) -> str:
        """Return the visible text of the active page."""
        try:
            text = self.page.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception:
            return ""
        return (text or "")[:limit]

    def links(self, *, keywords: Optional[List[str]] = None) -> List[str]:
        """Return every external link of the active page."""
        try:
            found = self.page.evaluate(
                "() => Array.from(document.querySelectorAll('a[href]')).map(a => a.href)"
            )
        except Exception:
            return []
        links = []
        for link in found or []:
            if not isinstance(link, str) or not link.startswith("http"):
                continue
            if keywords and not any(keyword.lower() in link.lower() for keyword in keywords):
                continue
            if link not in links:
                links.append(link)
        return links

    def wait_for(
        self,
        predicate,
        *,
        timeout_ms: Optional[int] = None,
        interval_ms: int = 1000,
    ) -> bool:
        """Poll *predicate* until it returns a truthy value or time runs out."""
        deadline = time.monotonic() + (timeout_ms or self.timeout_ms) / 1000
        while time.monotonic() < deadline:
            try:
                if predicate(self):
                    return True
            except Exception:
                pass
            time.sleep(interval_ms / 1000)
        return False
