"""Drive a real browser through the Cloudflare challenge, then harvest
the Naruto Shippuden season 18 episode URLs and export a cookies.txt
that yt-dlp can reuse.

Usage:  python3 tools/browse_site.py <site-url> "<search terms>"
"""

from __future__ import annotations

import http.cookiejar
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

COOKIE_FILE = Path("/workspace/data/cookies.txt")


def export_cookies(playwright_context) -> int:
    """Write the browser cookies as a Netscape cookies.txt for yt-dlp."""
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    jar = http.cookiejar.MozillaCookieJar(str(COOKIE_FILE))
    for cookie in playwright_context.cookies():
        domain = cookie["domain"]
        include_sub = "TRUE" if domain.startswith(".") else "FALSE"
        secure = "TRUE" if cookie.get("secure") else "FALSE"
        expires = cookie.get("expires", -1)
        expires = str(int(expires)) if expires and expires > 0 else "0"
        jar.set_cookie(
            http.cookiejar.Cookie(
                version=0, name=cookie["name"], value=cookie["value"],
                port=None, port_specified=False,
                domain=domain, domain_specified=True, domain_initial_dot=domain.startswith("."),
                path=cookie.get("path", "/"), path_specified=True,
                secure=bool(cookie.get("secure")), expires=expires, discard=False,
                comment=None, comment_url=None, rest={},
            )
        )
    jar.save(ignore_discard=True, ignore_expires=True)
    return len(playwright_context.cookies())


def wait_for_challenge(page, timeout=90):
    """Wait until Cloudflare's interstitial disappears."""
    start = time.time()
    while time.time() - start < timeout:
        title = (page.title() or "").lower()
        if "just a moment" not in title and "attention required" not in title:
            return True
        time.sleep(1)
    return False


def main() -> int:
    site = sys.argv[1] if len(sys.argv) > 1 else "https://toonworld4all.me/"
    terms = sys.argv[2] if len(sys.argv) > 2 else "naruto shippuden season 18"

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--lang=en-US",
            ],
        )
        context = browser.new_context(
            user_agent=UA,
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            timezone_id="America/New_York",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        page = context.new_page()

        print(f"==> {site}")
        page.goto(site, wait_until="domcontentloaded", timeout=90_000)
        ok = wait_for_challenge(page)
        print(f"==> challenge cleared: {ok} (title: {page.title()!r})")

        if not ok:
            print("==> could not clear the challenge")
            browser.close()
            return 2

        count = export_cookies(context)
        print(f"==> exported {count} cookies -> {COOKIE_FILE}")

        # Search the site for the requested content.
        search_url = (
            f"{urlparse(site).scheme}://{urlparse(site).netloc}"
            f"/?s={terms.replace(' ', '+')}"
        )
        print(f"==> search: {search_url}")
        page.goto(search_url, wait_until="domcontentloaded", timeout=90_000)
        wait_for_challenge(page, timeout=60)
        time.sleep(2)

        links = page.eval_on_selector_all(
            "article a[href], .post a[href], h2 a[href], h3 a[href]",
            "els => els.map(e => ({href: e.href, text: (e.textContent||'').trim()}))",
        )

        seen = set()
        found = []
        for link in links:
            href = link["href"].split("#")[0]
            if href in seen:
                continue
            seen.add(href)
            if re.search(r"naruto|shippuden", href, re.I) or re.search(r"naruto|shippuden", link["text"], re.I):
                found.append(link)

        print(f"==> {len(found)} candidate links")
        for link in found[:40]:
            print(f"    {link['text'][:70]:<72} {link['href']}")

        if not found:
            # Fall back to the site index and its internal search box.
            print("==> no matches in search results; dumping article links from the index")
            page.goto(site, wait_until="domcontentloaded", timeout=90_000)
            wait_for_challenge(page, timeout=60)
            time.sleep(2)
            for link in page.eval_on_selector_all(
                "a[href]", "els => els.map(e => ({href: e.href, text: (e.textContent||'').trim()}))"
            ):
                if re.search(r"naruto|shippuden", link["href"], re.I):
                    print(f"    {link['text'][:70]:<72} {link['href']}")

        page.screenshot(path="/workspace/site_search.png", full_page=False)
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
