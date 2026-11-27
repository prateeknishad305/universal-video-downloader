"""Capture the web UI while it analyses a MultiMovies episode."""

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.sources.embed_resolver import find_chromium  # noqa: E402

BASE = "http://127.0.0.1:8000"
EPISODE = "https://m1.multimovies.courses/series/whats-new-scooby-doo-57052/season/3/episode/14"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path=find_chromium(),
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(user_agent=UA, viewport={"width": 1500, "height": 980}, locale="en-US")
        context.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = context.new_page()

        page.goto(BASE, wait_until="networkidle", timeout=60000)
        time.sleep(1.5)
        page.screenshot(path="/workspace/ui_4_multimovies.png")
        print("saved ui_4_multimovies.png")

        page.fill("#urlInput", EPISODE)
        page.click("#analyzeBtn")
        page.wait_for_function(
            "() => { const el = document.querySelector('#results');"
            " return el && el.innerText.trim().length > 40; }",
            timeout=120000,
        )
        time.sleep(3)
        page.screenshot(path="/workspace/ui_5_multimovies_qualities.png")
        print("saved ui_5_multimovies_qualities.png")

        body = page.inner_text("body")
        for line in body.splitlines():
            line = line.strip()
            if line and any(k in line for k in ("1080", "720", "480", "360", "server", "Server")):
                print("  |", line[:110])

        browser.close()


if __name__ == "__main__":
    main()
