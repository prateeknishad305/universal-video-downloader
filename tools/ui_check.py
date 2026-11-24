"""Drive the web UI in a browser and capture the result."""

import sys
import time

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
SEASON = "https://toonworld4all.me/naruto-shippuden-season-18-episodes-bluray-hindi-multi-audio/"
EPISODE = "https://archive.toonworld4all.me/episode/naruto-shippuden-1x373"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def shot(page, name):
    page.screenshot(path=f"/workspace/ui_{name}.png")
    print(f"  saved ui_{name}.png")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(user_agent=UA, viewport={"width": 1500, "height": 980}, locale="en-US")
        context.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = context.new_page()

        page.goto(BASE, wait_until="networkidle", timeout=60000)
        time.sleep(2)
        print("loaded:", page.title())
        shot(page, "1_home")

        # --- season page -> playlist -----------------------------------
        page.fill("#urlInput", SEASON)
        page.click("#analyzeBtn")
        page.wait_for_selector(".entry", timeout=90000)
        time.sleep(1.5)
        entries = page.eval_on_selector_all(".entry-title", "els=>els.map(e=>e.textContent.trim())")
        print(f"playlist entries: {len(entries)} -> {entries[:3]}")
        shot(page, "2_playlist")

        # --- episode page -> quality ladder -----------------------------
        page.fill("#urlInput", EPISODE)
        page.click("#analyzeBtn")
        page.wait_for_selector("#qualitySelect", timeout=90000)
        time.sleep(1.5)
        options = page.eval_on_selector_all(
            "#qualitySelect option", "els=>els.map(e=>e.textContent.trim())")
        print(f"quality options: {len(options)}")
        for option in options:
            print("   ", option)
        shot(page, "3_episode")

        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
