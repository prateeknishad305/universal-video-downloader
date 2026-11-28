"""Resolve JS-driven streaming embeds by loading them in a real browser.

Many streaming embed pages build their <video> source at runtime, so the
media URL never appears in the initial HTML.  Loading the page in Chromium and
watching the network for .m3u8 / .mp4 responses finds it regardless of how the
provider obfuscates it.
"""

from __future__ import annotations

import json
import re
import sys
import time

from playwright.sync_api import sync_playwright

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

MEDIA_RE = re.compile(r"\.(m3u8|mp4|m4v|webm|mkv)(\?|$)", re.I)


def find_chromium():
    """Locate an installed Chromium build for Playwright."""
    import glob
    import os

    patterns = [
        os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux/chrome"),
        os.path.expanduser("~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"),
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
    ]
    for pattern in patterns:
        for candidate in sorted(glob.glob(pattern)):
            if os.access(candidate, os.X_OK):
                return candidate
    return None


def main(urls):
    executable = find_chromium()
    print(f"chromium: {executable or 'playwright default'}")

    results = {}
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
            user_agent=UA,
            viewport={"width": 1440, "height": 900},
            locale="en-US",
        )
        context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )

        for name, url in urls:
            page = context.new_page()
            media = []
            page.on(
                "response",
                lambda r: media.append((r.status, r.url)) if MEDIA_RE.search(r.url) else None,
            )
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                for _ in range(20):
                    if "just a moment" not in (page.title() or "").lower():
                        break
                    time.sleep(1)
                time.sleep(8)
                try:
                    page.evaluate(
                        "const v=document.querySelector('video');"
                        "if(v){v.muted=true;v.play().catch(()=>{});}"
                    )
                except Exception:
                    pass
                time.sleep(5)
            except Exception as exc:
                print(f"  {name}: navigation error {str(exc)[:80]}")
            results[name] = {"url": url, "media": media, "title": page.title()}
            print(f"\n{name}: {page.title()[:70]}")
            if media:
                for status, media_url in media[:6]:
                    print(f"    [{status}] {media_url[:150]}")
            else:
                print("    (no media request captured)")
            page.close()

        browser.close()

    json.dump(results, open("/workspace/data/mm_embeds.json", "w"), indent=2)
    return 0


if __name__ == "__main__":
    config = json.load(open("/workspace/data/mm_watchconfig.json"))
    pairs = [(s["name"], s["url"]) for s in config["initialServers"]]
    if len(sys.argv) > 1:
        wanted = sys.argv[1:]
        pairs = [p for p in pairs if p[0] in wanted]
    sys.exit(main(pairs))
