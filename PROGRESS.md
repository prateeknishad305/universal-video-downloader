# Progress

## 2026-11-27 - Render hosting, cold start and memory ceiling

The service is now shaped for a 512 MB Render instance: it boots in about
0.7 s, answers the health check without a token and stays around 110 MB of
RAM while a download runs. The upstream work (multimovies, embed resolvers)
was merged into the local toonworld branch, so both features are in one tree.

### Modules

| Module | What it does | State |
| --- | --- | --- |
| `app/config.py` | Render block, browser switch, file size cap, history limit, free disk guard, ffmpeg thread cap, `curl_cffi` detection | done |
| `app/ytdlp_client.py` | Lazy yt-dlp import, `cachedir: False`, `max_filesize`, ffmpeg thread args, MEGA failover, placeholder check | done |
| `app/main.py` | Public `/api/health`, lazy Telegram import, version from `importlib.metadata` | done |
| `app/jobs.py` | `_trim_jobs()` keeps the job list bounded, `_disk_space_problem()` guards new downloads | done |
| `app/sources/browser.py` | Browser route can be switched off with `UVD_BROWSER_ENABLED=0` | done |
| `app/storage.py` | pymongo is imported only when a Mongo URI is set | done |
| `Dockerfile` | Slim image, optional Chromium build, memory friendly environment, non-root user, health check | done |
| `render.yaml` | Blueprint: docker runtime, `/api/health`, 10 GB `/data` disk, generated `API_TOKEN` | done |
| `Procfile` | `uvicorn` with `--proxy-headers` and a keep-alive timeout | done |
| `requirements-browser.txt` | Playwright, kept out of the core requirements | done |
| `run.sh` | Installs the browser requirements only when the browser route is on | done |
| `app/jobs.py` | `_disk_free_mb()`, a size aware `_disk_space_problem()` and a guard that stops a download when the disk fills up | done |

### Endpoints

| Endpoint | Method | Answer | State |
| --- | --- | --- | --- |
| `/api/health` | GET | Public on Render; version, ffmpeg, browser, `max_file_mb`, `free_disk_mb`, `render` | done |
| `/api/sites` | GET | The supported sites, token protected outside of health | done |
| `/api/jobs` | POST | A download job, size capped by `UVD_MAX_FILE_MB` | done |
| `/api/jobs/{job_id}` | GET | One job, `UVD_MAX_HISTORY_JOBS` keeps the list short | done |
| `/api/jobs/{job_id}/file` | GET | The finished file | done |

### Settings

| Variable | Meaning |
| --- | --- |
| `UVD_BROWSER_ENABLED` | `0` keeps Chromium out of the process |
| `UVD_MAX_FILE_MB` | Largest file yt-dlp may fetch, `0` means no limit |
| `UVD_MAX_HISTORY_JOBS` | How many finished jobs stay in memory |
| `UVD_MIN_FREE_DISK_MB` | Free space that has to remain before a download starts |
| `UVD_FFMPEG_THREADS` | Threads ffmpeg may use while merging |

### Tests

- `pytest -q` - 99 passed, 2 skipped (also green without Playwright installed).
- `merge_threads_test.py` - ffmpeg still merges a video and an audio stream
  with `-threads 2`: the result holds one `h264` video and one `aac` audio
  stream.
- Two new tests in `tests/test_api.py` cover the disk guard: a download that
  is refused when the disk is nearly full and a download that stops when the
  disk fills up while it runs.

### Live state from this machine

- A Render-like run (`RENDER=1`, one worker, browser off) answered
  `/api/health` without a token, listed 1751 sites with the token and finished
  a download of 5485935 bytes that `ffprobe` reads as 10.24 seconds.
- The peak memory of that run was 109 MB, the import of the app 0.661 s with
  yt-dlp, Telegram and pymongo still unimported.
- A 1 GiB file (1073741824 bytes) was downloaded through `/api/jobs` in 401
  seconds. The server stayed at 104 MB and peaked at 112 MB, so the size of
  the file costs disk and time, not memory.
- Docker is not installed in this box, so the image was reviewed by hand and
  not built here.
- `render.yaml` was checked against the official Render blueprint schema
  (`https://render.com/schema/render.yaml.json`) and validates.

### Big movies

- `UVD_MAX_FILE_MB=2048` caps a download at 2 GB; yt-dlp aborts a bigger file
  when the host announces its size.
- A merge of a separate video and audio stream needs about twice the size of
  the movie on the disk while it runs.
- The disk guard refuses a download when less than `UVD_MIN_FREE_DISK_MB` is
  free and stops a running download when the disk fills up, instead of ending
  in a half written file.

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
