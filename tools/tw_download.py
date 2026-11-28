"""Try a real download from the app for one toonworld4all.me title."""
import sys, time, httpx
BASE="http://127.0.0.1:8000"
URL=sys.argv[1]; Q=sys.argv[2] if len(sys.argv)>2 else "480p-h264-8bit"
c=httpx.Client(timeout=180)
a=c.post(f"{BASE}/api/analyze", json={"url":URL}).json()
print("title:", a.get("title"))
print("qualities:", [q["id"] for q in a.get("qualities",[])])
j=c.post(f"{BASE}/api/jobs", json={"url":a.get("webpage_url") or URL,"plan_id":a.get("plan_id"),
                                   "quality_id":Q,"mode":"video"}).json()
jid=j["id"]; print("job:", jid)
last=""
while True:
    job=c.get(f"{BASE}/api/jobs/{jid}").json()
    line=f"{job['status']:<10} {job['stage'][:30]:<32} {round(job.get('progress',0),1):>6}%"
    if line!=last: print("  ",line); last=line
    if job["status"] in ("completed","error","cancelled"): break
    time.sleep(2)
print("\nfinal:", job["status"], "|", job.get("filename"))
for l in (job.get("logs") or [])[-8:]: print("   ", l[:190])
