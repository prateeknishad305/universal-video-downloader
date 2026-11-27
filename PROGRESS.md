# Progress

## 2026-11-26 - toonworld4all.me download path

The native toonworld support was rebuilt around the way the archive actually
works: every file link is an encrypted redirect token that opens an
interstitial page, that page names the host and the file id, and the real
files are guarded by an ad shortener. Each host now has its own resolver and
the download refuses to hand over a placeholder file.

### Modules

| Module | What it does | State |
| --- | --- | --- |
| `app/sources/toonworld.py` | Season and episode pages, the interstitial (`link.domain`, `link.hidden`, `destination`), the browser route through the shortener, the resolution pipeline | done |
| `app/sources/toonworld_hosts.py` | Filepress (`/api/file/get`), GDFlix (Google Drive link), HubCloud (challenge + `api/downloader.php`), MEGA and AppDrive resolvers | done |
| `app/sources/mega.py` | MEGA API client, file key decoding, AES-128-CTR decryption (`cryptography` or `openssl`) | done |
| `app/sources/browser.py` | Chromium session (Playwright) with a persistent profile, one driver per thread, shared lock | done |
| `app/sources/http.py` | HTTP helper with Chrome impersonation | done |
| `app/ytdlp_client.py` | Host failover, MEGA download, placeholder check of the finished file, structured yt-dlp impersonation target | done |
| `app/config.py` | Browser and placeholder settings | done |

### Endpoints

| Endpoint | Method | Answer | State |
| --- | --- | --- | --- |
| `/api/health` | GET | The state of the service, the tools it found, the Chromium build | done |
| `/api/sites` | GET | The sites that can be downloaded | done |
| `/api/analyze` | POST | The qualities of a link, with the hosts of every tier | done |
| `/api/jobs` | POST | A download job | done |
| `/api/jobs/batch` | POST | Several download jobs | done |
| `/api/jobs` | GET | The running and finished jobs | done |
| `/api/history` | GET | The job history | done |
| `/api/jobs/{job_id}` | GET | One job | done |
| `/api/jobs/{job_id}/events` | GET | The progress of a job (SSE) | done |
| `/api/jobs/{job_id}/cancel` | POST | Stops a running job | done |
| `/api/jobs/{job_id}` | DELETE | Removes a finished job | done |
| `/api/clear-finished` | POST | Removes every finished job | done |
| `/api/jobs/{job_id}/file` | GET | The finished file | done |

### Settings

| Variable | Meaning |
| --- | --- |
| `UVD_BROWSER_EXECUTABLE` | Path of the Chromium build |
| `UVD_BROWSER_HEADLESS` | `1` runs the browser without a window |
| `UVD_BROWSER_PROFILE` | Directory that keeps the cookies of a challenge |
| `UVD_BROWSER_TIMEOUT_MS` | How long the browser waits for a page |
| `UVD_MIN_MEDIA_BYTES` | Size below which a file counts as a placeholder |

### Tests

- `tests/test_toonworld.py` - the pages, the tokens and the host checks.
- `tests/test_toonworld_hosts.py` - the five host resolvers.
- `tests/test_toonworld_resolution.py` - the resolution pipeline, the
  placeholder check and the impersonation target.
- `pytest -q` - 85 passed, 2 skipped.

### Live state from this machine

- The archive stores placeholder ids: every host answers "the file does not
  exist" for them.
- The real links sit behind the ad shortener of the interstitial. The browser
  route opens it and waits for the page to verify itself; the verification is
  never clicked. On this network the shortener shows a Cloudflare checkbox
  that never clears on its own, so the job ends with that reason instead of a
  broken file.
- A toonworld job now ends with the exact reason per host and no file is
  written.
- A normal site still downloads end to end: `sample-10s.mp4` finished as
  5485935 bytes and `ffprobe` reads 10.24 seconds of video.
- MEGA refuses API requests from this network (it answers `-2` for every
  command), so the MEGA resolver reports that instead of failing silently.

### Fixes on this date

- yt-dlp needs a structured impersonation target when it is used as a library;
  a plain string crashed every host attempt with `AssertionError`. The target
  is now built with `ImpersonateTarget.from_str`.
- The finished file is checked against the size the archive announced, so a
  truncated host answer cannot pass as the episode.
