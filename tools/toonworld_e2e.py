"""End-to-end check for every kind of toonworld4all.me URL."""

import sys
import time

import httpx

BASE = "http://127.0.0.1:8000"

CASES = [
    ("archive episode", "https://archive.toonworld4all.me/episode/naruto-shippuden-1x373"),
    ("archive movie", "https://archive.toonworld4all.me/movie/steps-2026"),
    ("front-end movie", "https://toonworld4all.me/steps-2026-multi-audio-hindi/"),
    ("show page", "https://toonworld4all.me/naruto-shippuden-season-18-episodes-bluray-hindi-multi-audio/"),
    ("category page", "https://toonworld4all.me/category/netflix/"),
    ("tag page", "https://toonworld4all.me/tag/animated-movies"),
    ("movies list", "https://toonworld4all.me/all-movies-list_25/"),
    ("home page", "https://toonworld4all.me/"),
]


def main():
    client = httpx.Client(timeout=180)
    for label, url in CASES:
        try:
            response = client.post(f"{BASE}/api/analyze", json={"url": url})
            if response.status_code != 200:
                print(f"{label:<16} HTTP {response.status_code}  {response.text[:120]}")
                continue
            data = response.json()
            if data.get("kind") == "playlist":
                entries = data.get("entries", [])
                print(f"{label:<16} PLAYLIST  {data.get('playlist_count'):>4} items  | {data.get('title','')[:34]}")
                if entries:
                    print(f"{'':<16}   first: {entries[0]['url'][:76]}")
            else:
                qualities = [q["id"] for q in data.get("qualities", [])]
                print(f"{label:<16} VIDEO     {len(qualities)} qualities {qualities[:4]}")
                print(f"{'':<16}   {data.get('title','')[:60]}")
        except Exception as exc:  # noqa: BLE001
            print(f"{label:<16} ERROR     {str(exc)[:110]}")


if __name__ == "__main__":
    main()
