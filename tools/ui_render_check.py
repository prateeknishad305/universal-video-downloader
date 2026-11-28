"""Check that the UI stays light: bounded DOM, no runaway memory."""

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.sources.embed_resolver import find_chromium  # noqa: E402

BASE = "http://127.0.0.1:8000"
SEASON = "https://toonworld4all.me/naruto-shippuden-season-18-episodes-bluray-hindi-multi-audio/"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def node_count(page):
    return page.evaluate("document.querySelectorAll('*').length")


def heap_mb(page):
    return page.evaluate(
        "() => performance.memory ? performance.memory.usedJSHeapSize / 1048576 : 0"
    )


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path=find_chromium(),
            args=["--no-sandbox", "--disable-dev-shm-usage", "--enable-precise-memory-info"],
        )
        context = browser.new_context(user_agent=UA, viewport={"width": 1500, "height": 980})
        page = context.new_page()

        page.goto(BASE, wait_until="networkidle", timeout=60000)
        time.sleep(2)
        print(f"home            nodes={node_count(page):>6}"
              f"  heap={heap_mb(page):6.1f} MB")

        # --- the supported-sites modal (1754 chips) -----------------------
        page.click("#sitesBtn")
        time.sleep(2)
        chips = page.evaluate("document.querySelectorAll('.site-chip').length")
        print(f"sites modal     nodes={node_count(page):>6}"
              f"  chips={chips}  heap={heap_mb(page):6.1f} MB")

        start = time.time()
        page.fill("#sitesSearch", "youtube")
        time.sleep(0.6)
        visible = page.evaluate(
            "Array.from(document.querySelectorAll('.site-chip')).filter(c => !c.hidden).length"
        )
        elapsed = (time.time() - start) * 1000
        print(f"filter 'youtube' visible={visible} in {elapsed:.0f} ms"
              f"  nodes={node_count(page)}"
              f"  heap={heap_mb(page):6.1f} MB")
        page.fill("#sitesSearch", "")
        time.sleep(0.4)
        page.click("#closeSitesBtn")
        page.wait_for_selector("#sitesModal", state="hidden", timeout=10000)
        time.sleep(0.5)

        # --- a playlist (18 episodes) -------------------------------------
        page.fill("#urlInput", SEASON)
        page.click("#analyzeBtn")
        try:
            page.wait_for_function(
                "() => { const el = document.querySelector('#entryList');"
                " return el && el.querySelectorAll('.entry').length > 0; }",
                timeout=120000,
            )
        except Exception:
            hint = page.inner_text("#hint") if page.query_selector("#hint") else ""
            print("playlist wait failed, hint:", hint[:200])
            raise
        time.sleep(1)
        rows = page.evaluate("document.querySelectorAll('.entry').length")
        print(f"playlist        nodes={node_count(page):>6}"
              f"  rows={rows}  heap={heap_mb(page):6.1f} MB")
        page.screenshot(path="/workspace/ui_6_playlist_paged.png")
        print("saved ui_6_playlist_paged.png")

        # --- the single-video analysis view -------------------------------
        page.fill("#urlInput", "https://archive.toonworld4all.me/episode/naruto-shippuden-1x373")
        page.click("#analyzeBtn")
        page.wait_for_function(
            "() => { const el = document.querySelector('#qualitySelect'); return !!el; }",
            timeout=120000,
        )
        time.sleep(1)
        page.screenshot(path="/workspace/ui_7_render.png")
        print(f"analysis        nodes={node_count(page):>6}"
              f"  heap={heap_mb(page):6.1f} MB")
        print("saved ui_7_render.png")

        browser.close()


if __name__ == "__main__":
    main()
