"""End-to-end check: analyse a MultiMovies episode and download it."""

import sys
import time

import httpx

BASE = "http://127.0.0.1:8000"
EPISODE = "https://m1.multimovies.courses/series/whats-new-scooby-doo-57052/season/3/episode/14"
QUALITY = sys.argv[1] if len(sys.argv) > 1 else "720p"

client = httpx.Client(timeout=180)

print("analyze...")
response = client.post(f"{BASE}/api/analyze", json={"url": EPISODE})
print("HTTP", response.status_code)
analysis = response.json()
print("title:", analysis.get("title"))
print("qualities:", [q["id"] for q in analysis.get("qualities", [])])
for server in analysis.get("servers", []):
    print(f"   {server['name']:<12} {server['state']:<11} {server['detail'][:60]}")

print(f"\nstarting download at {QUALITY}")
created = client.post(
    f"{BASE}/api/jobs",
    json={"url": EPISODE, "plan_id": analysis.get("plan_id"), "quality_id": QUALITY, "mode": "video"},
)
print("create:", created.status_code, created.json().get("id"))
job_id = created.json()["id"]

last = ""
while True:
    job = client.get(f"{BASE}/api/jobs/{job_id}").json()
    line = f"{job['status']:<12} {job['stage']:<24} {job['progress']:>5.1f}%"
    if line != last:
        print("  ", line)
        last = line
    if job["status"] in ("completed", "error", "cancelled"):
        break
    time.sleep(2)

print("\nfinal:", job["status"], "|", job.get("filename"))
for entry in (job.get("logs") or [])[-10:]:
    print("   ", entry[:170])
if job["status"] == "completed":
    served = client.get(f"{BASE}/api/jobs/{job_id}/file")
    print("served bytes:", len(served.content), "type:", served.headers.get("content-type"))
