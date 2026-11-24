"""End-to-end check of the running API: queue jobs and follow them."""

import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000"
client = httpx.Client(timeout=120.0)


def show(title):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def follow(job_id, label, timeout=240):
    start = time.time()
    last = ""
    while time.time() - start < timeout:
        job = client.get(f"{BASE}/api/jobs/{job_id}").json()
        stage = f"{job['status']:<12} {job['stage']:<28} {job['progress']:>6.1f}%"
        if stage != last:
            extra = ""
            if job.get("speed"):
                extra = f" {job['speed'] / 1_048_576:.2f} MB/s"
            if job.get("eta") is not None:
                extra += f" eta {job['eta']}s"
            print(f"  [{label}] {stage}{extra}")
            last = stage
        if job["status"] in {"completed", "error", "cancelled"}:
            print(f"  [{label}] FINAL: {job['status']}")
            if job.get("filename"):
                print(f"  [{label}] file: {job['filename']}")
            if job.get("error"):
                print(f"  [{label}] error: {job['error'][:600]}")
            for line in (job.get("logs") or [])[-14:]:
                print(f"  [{label}]   log: {line[:190]}")
            return job
        time.sleep(1.0)
    print(f"  [{label}] timed out")
    return {}


def start(payload, label):
    response = client.post(f"{BASE}/api/jobs", json=payload)
    print(f"  [{label}] create -> HTTP {response.status_code}")
    response.raise_for_status()
    return response.json()["id"]


show("1. Direct MP4 (proves the core download path)")
job_id = start(
    {
        "url": "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/360/Big_Buck_Bunny_360_10s_1MB.mp4",
        "quality_id": "best",
    },
    "mp4",
)
job = follow(job_id, "mp4")
if job.get("status") == "completed":
    saved = client.get(f"{BASE}/api/jobs/{job_id}/file")
    print(f"  [mp4] served bytes: {len(saved.content)}  content-type: {saved.headers.get('content-type')}")

show("2. toonworld4all.me - Naruto Shippuden S18 episode 373")
job_id = start(
    {
        "url": "https://archive.toonworld4all.me/episode/naruto-shippuden-1x373",
        "quality_id": "1080p-h265-hq-10bit",
        "mode": "video",
    },
    "toon",
)
follow(job_id, "toon")

show("3. Job list")
jobs = client.get(f"{BASE}/api/jobs").json()["jobs"]
for job in jobs:
    print(f"  {job['id']}  {job['status']:<10} {job['title'][:58]}")
