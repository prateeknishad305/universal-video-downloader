"""Scan the whole toonworld4all.me catalogue for episodes that actually resolve.

For a sample of shows it walks season pages -> episode pages -> host
destinations and reports which hosts are alive, so we can tell a broken
extractor apart from dead source files.
"""

from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/workspace")

from app.sources import http, toonworld  # noqa: E402

SITE = "https://toonworld4all.me"

SHOWS = [
    "one piece", "bleach", "attack on titan", "dragon ball super",
    "naruto shippuden", "hunter x hunter", "demon slayer", "jujutsu kaisen",
    "boruto", "my hero academia", "death note", "fullmetal alchemist",
]


def search_pages(term: str):
    try:
        status, body, _ = http.fetch(f"{SITE}/?s={term.replace(' ', '+')}")
        if status >= 400:
            return []
    except Exception:
        return []
    slugs = re.findall(r'toonworld4all\.me/([a-z0-9\-]{8,60})/', body)
    slugs += re.findall(r'href="(/[a-z0-9\-]{8,60}/)"', body)
    slugs = [s.strip("/") for s in slugs]
    skip = {"search", "comments", "first-on-ne", "exclusive-shows", "contact-us", "wp-admin"}
    junk = ("wp-content", "wp-includes", "wp-json", "category", "tag", "feed", "author",
            "page", "search", "all-movies", "anime-shows-list", "cartoon-shows-list",
            "tv-shows-list", "all-", "-list")
    seen, out = set(), []
    for slug in slugs:
        if slug in seen or slug in skip or slug.startswith(junk):
            continue
        seen.add(slug)
        out.append(f"{SITE}/{slug}/")
    return out


def season_episodes(url: str):
    try:
        status, body, _ = http.fetch(url)
        if status >= 400:
            return []
    except Exception:
        return []
    pairs = []
    for m in re.finditer(
        r'mks_accordion_heading[^>]*><strong>(?P<title>[^<]+)</strong>.*?'
        r'href="(?P<url>https?://[^"]+/redirect/[a-f0-9]+)"',
        body, re.S,
    ):
        pass
    # episode links live on the archive host
    for m in re.finditer(rf"https?://{re.escape(toonworld.ARCHIVE)}/episode/([a-z0-9\-]+)", body):
        slug = m.group(1)
        if slug not in [s for s, _ in pairs]:
            pairs.append((slug, f"https://{toonworld.ARCHIVE}/episode/{slug}"))
    return pairs


def check_episode(url: str):
    try:
        analysis = toonworld.probe_episode(url)
    except Exception as exc:
        return {"url": url, "error": str(exc)[:120]}
    meta = analysis["metadata"] if "metadata" in analysis else {}
    result = {
        "url": url,
        "title": analysis["title"],
        "qualities": len(analysis["qualities"]),
        "streams": len(analysis.get("streams") or []),
        "hosts": {},
    }
    for quality in analysis["qualities"][:2]:
        for source in quality["sources"]:
            destination, error = toonworld.resolve_destination(source["token"])
            state = {"state": "unknown", "detail": error or ""}
            if destination:
                state = toonworld.probe_destination(destination)
            result["hosts"][source["host"]] = state.get("state")
    return result


def main():
    results = []
    all_seasons = []
    for show in SHOWS:
        pages = search_pages(show)
        for page in pages[:2]:
            all_seasons.append((show, page))
    print(f"season pages found: {len(all_seasons)}")

    episodes = []
    seen_ep = set()
    for show, page in all_seasons:
        if len(episodes) >= 12:
            break
        eps = season_episodes(page)
        if eps and eps[0][0] not in seen_ep:
            seen_ep.add(eps[0][0])
            episodes.append((show, eps[0]))
    print(f"probing {len(episodes)} episodes\n")

    def run(item):
        show, (slug, url) = item
        out = check_episode(url)
        out["show"] = show
        return out

    with ThreadPoolExecutor(max_workers=5) as ex:
        for res in ex.map(run, episodes):
            results.append(res)
            alive = [h for h, s in res.get("hosts", {}).items() if s == "ready"]
            blocked = [h for h, s in res.get("hosts", {}).items() if s == "blocked"]
            print(f"{res.get('show','')[:18]:<20} {str(res.get('title'))[:44]:<46} "
                  f"q={res.get('qualities')} streams={res.get('streams')} "
                  f"hosts={res.get('hosts')} alive={alive} blocked={blocked}")

    json.dump(results, open("/workspace/data/site_scan.json", "w"), indent=2, default=str)
    ready = [r for r in results if any(s == "ready" for s in r.get("hosts", {}).values())]
    print(f"\nepisodes with a reachable host: {len(ready)} / {len(results)}")
    for r in ready[:10]:
        print("   READY:", r["title"][:60], r["hosts"])


if __name__ == "__main__":
    main()
