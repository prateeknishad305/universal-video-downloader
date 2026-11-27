# Universal Video Downloader

A self-hosted web app that downloads **video and audio from ~1,800 different sites** —
YouTube, TikTok, Twitter/X, Instagram, Facebook, Reddit, Vimeo, Dailymotion, Bilibili,
SoundCloud, Twitch, Pinterest, Rumble, OK.ru, Niconico and hundreds more.

It is a polished front end over [`yt-dlp`](https://github.com/yt-dlp/yt-dlp), the
community-maintained downloader that already handles every site-specific quirk,
signature scheme, API change and anti-bot workaround. This project adds the parts
yt-dlp does not do: a live progress dashboard, a quality picker, playlist handling,
batch queues, cancellation, and a clean API you can script against.

---

## Features

| | |
|---|---|
| **1,700+ sites** | Every plugin shipped with yt-dlp, listed live in the UI |
| **Native site support** | `toonworld4all.me` and `multimovies.wtf` are understood natively: season pages, playlists, quality tiers, per-host/per-server failover |
| **Cloudflare-ready** | Browser impersonation (`curl_cffi`) clears bot challenges without a headless browser |
| **Quality picker** | Best / 4K / 1080p / 720p / … / audio-only, with size estimates |
| **Live progress** | Server-sent events stream bytes, speed and ETA as you download |
| **Audio extraction** | Pull the MP3 (192 kbps) or keep the original audio codec |
| **Playlists** | Expand a playlist, tick the videos you want, queue them all |
| **Batch mode** | Paste many links and download them in parallel |
| **Cancellation** | Stop a download mid-flight; partial files are cleaned up |
| **Subtitles** | Optionally embed subtitles when the source has them |
| **Auth for gated sites** | Use browser cookies for age- or login-gated content |
| **Telegram bot** | Send a link in Telegram, pick a quality with buttons, get the file back |
| **MongoDB history** | Optional persistent job history; falls back to memory |
| **Headless CLI** | Script the same engine from the terminal |
| **REST API** | Every feature is exposed over JSON |
| **No build step** | Plain HTML/CSS/JS — nothing to compile |

---

## Quick start

### 1. The launcher (recommended)

```bash
git clone <your-repo> universal-downloader
cd universal-downloader
chmod +x run.sh
./run.sh
```

Open **<http://127.0.0.1:8000>**.

The script creates a virtualenv, installs the dependencies and starts the server.
You need **Python 3.9+** and **ffmpeg** on your PATH.

### 2. Docker

```bash
docker compose up --build
```

Open <http://127.0.0.1:8000>. Files land in `./data/downloads`.

### 3. Manual

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The browser route needs Playwright and a Chromium build. They are kept out of
the core requirements because they add 565 MB to the install:

```bash
.venv/bin/pip install -r requirements-browser.txt
.venv/bin/python -m playwright install chromium
```

### Install ffmpeg

Merging separate video+audio streams and converting to MP3 both need ffmpeg.
Without it the app still works, but only for sites that serve a single
pre-muxed file.

```bash
# Debian / Ubuntu
sudo apt install ffmpeg
# macOS
brew install ffmpeg
# Windows (winget)
winget install Gyan.FFmpeg
```

---

## Using it

1. **Paste a link** into the field at the top and press *Analyze*.
2. The app detects the site and lists every available format.
3. **Pick a quality** — the dropdown is grouped into *Video* and *Audio only*,
   with an estimated file size next to each option.
4. Press **Download**. Progress, speed and ETA stream live into the
   *Downloads* panel on the right.
5. When it finishes, hit **Save to device**.

For a playlist link the app expands the entries instead, and you can tick the
ones you want and queue them all at once.

### Command line

The same engine works without the web UI:

```bash
# Download with the best quality
python -m app.cli "https://www.youtube.com/watch?v=XXXX"

# Audio only, as MP3
python -m app.cli "https://example.com/video" --audio

# See what is available first
python -m app.cli "https://example.com/video" --list-formats

# Batch from a file of links
python -m app.cli -f urls.txt -q v1080
```

---

## Configuration

Everything is controlled by environment variables (see `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `HOST` | `127.0.0.1` | Bind address. Use `0.0.0.0` to expose on the LAN |
| `PORT` | `8000` | Bind port |
| `DATA_DIR` | `./data` | Base directory for stored data |
| `DOWNLOAD_DIR` | `./data/downloads` | Where finished files are written |
| `MAX_CONCURRENT_JOBS` | `3` | Simultaneous downloads |
| `REQUEST_TIMEOUT` | `45` | Per-request network timeout (seconds) |
| `API_TOKEN` | *(empty)* | When set, every `/api` call needs this token |
| `COOKIES_FROM_BROWSER` | *(empty)* | e.g. `chrome`, `firefox`, `edge` |
| `COOKIES_FILE` | *(empty)* | Path to a Netscape `cookies.txt` |
| `PROXY` | *(empty)* | e.g. `http://127.0.0.1:8080` |
| `EMBED_SUBTITLES` | `0` | Embed subtitles by default |
| `MONGO_URI` | *(empty)* | MongoDB connection string for the job history |
| `MONGO_DB` | `universal_downloader` | Database name |
| `MONGO_COLLECTION` | `jobs` | Collection name |
| `TELEGRAM_BOT_TOKEN` | *(empty)* | Token from [@BotFather](https://t.me/BotFather); enables the bot |
| `TELEGRAM_ALLOWED_CHATS` | *(empty)* | Comma separated chat ids allowed to use the bot |
| `TELEGRAM_MAX_UPLOAD_MB` | `50` | Telegram's own upload limit |
| `PUBLIC_URL` | *(empty)* | Public base URL, used in bot messages |
| `UVD_BROWSER_ENABLED` | `1` | Set to `0` to run without the browser route |
| `UVD_BROWSER_EXECUTABLE` | *(auto)* | Path of the Chromium build |
| `UVD_BROWSER_HEADLESS` | `1` | `0` shows the browser window |
| `UVD_BROWSER_PROFILE` | `./data/browser-profile` | Profile that keeps the clearance cookies |
| `UVD_BROWSER_TIMEOUT_MS` | `45000` | How long the browser waits for a page |
| `UVD_MIN_MEDIA_BYTES` | `16384` | Below this a file counts as a placeholder |
| `UVD_MAX_FILE_MB` | `0` | Largest file that may be downloaded, `0` means no limit |
| `UVD_MAX_HISTORY_JOBS` | `200` | How many finished jobs are kept in memory |
| `UVD_MIN_FREE_DISK_MB` | `0` | Free space that has to stay for a new download |
| `UVD_FFMPEG_THREADS` | `2` | Threads ffmpeg may use while merging |

### Running it on your network

```bash
HOST=0.0.0.0 API_TOKEN=$(openssl rand -hex 16) ./run.sh
```

Then open `http://<your-ip>:8000/?token=<the-token>` once — the app stores the
token and strips it from the URL.

---

## Hosting on Render

Render runs the app as a Docker service. The repository ships everything that
is needed: `Dockerfile`, `Procfile` and `render.yaml`.

### 1. The blueprint (fastest)

1. Push the repository to GitHub.
2. In Render choose **New → Blueprint**, pick the repository and the
   `render.yaml` at its root.
3. Render creates the web service, a MongoDB database and a 1 GB disk for the
   downloads, then deploys.

The blueprint generates an `API_TOKEN`. Pass it as `x-api-token` (or
`?token=`) on every `/api` call; `/api/health` stays open so the platform can
check the service.

### 2. A plain Docker service

| Setting | Value |
|---|---|
| Runtime | Docker |
| Dockerfile path | `./Dockerfile` |
| Health check path | `/api/health` |
| Instance type | Starter (512 MB) or better |

### What keeps it fast and out of the OOM killer

| Measure | Why |
|---|---|
| `UVD_BROWSER_ENABLED=0` by default in the image | Chromium alone needs about 500 MB; without it the service stays near 110 MB |
| Playwright is not part of `requirements.txt` | The image stays small and the install stays quick. Build with `--build-arg INSTALL_BROWSER=1` when the browser route is wanted |
| One uvicorn worker, no `--workers` | Every worker copies the whole Python heap |
| `MAX_CONCURRENT_JOBS=1` | One download, one ffmpeg merge, one memory spike |
| `UVD_MAX_FILE_MB=2048` | yt-dlp refuses a file bigger than the cap, so the disk cannot fill up |
| `UVD_MIN_FREE_DISK_MB=256` | A download refuses to start when the disk is nearly full |
| `UVD_FFMPEG_THREADS=2` | ffmpeg stops from spawning a thread per core, which is what usually kills a small container |
| `UVD_MAX_HISTORY_JOBS=200` | The in-memory job list cannot grow without a limit |
| yt-dlp cache switched off (`cachedir: False`) | No growing cache directory in the container |
| `PYTHONMALLOC=malloc`, `MALLOC_ARENA_MAX=2`, `OMP_NUM_THREADS=1` | Fewer and smaller memory arenas |
| Lazy imports | yt-dlp, Telegram and pymongo are only imported when they are used, so the container answers the health check in about 0.7 s |
| `dumb-init` and `--proxy-headers` | Correct signal handling and the real client IP behind Render's proxy |

A download on the free plan needs about 110 MB of RAM and a little disk space
for the file itself. The Render disk (`/data`) keeps finished files between
deploys; without a disk the files disappear when the service restarts.

---

## API

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Version, ffmpeg availability, settings |
| `GET` | `/api/sites` | Every supported site plugin |
| `POST` | `/api/analyze` | Inspect a URL → title, thumbnail, formats, quality ladder |
| `POST` | `/api/jobs` | Queue a download → returns a job |
| `POST` | `/api/jobs/batch` | Queue up to 100 downloads at once |
| `GET` | `/api/jobs` | List all jobs |
| `GET` | `/api/history` | Previously recorded downloads (MongoDB or memory) |
| `GET` | `/api/jobs/{id}` | One job's state |
| `GET` | `/api/jobs/{id}/events` | Server-sent events with live progress |
| `POST` | `/api/jobs/{id}/cancel` | Cancel a running download |
| `DELETE` | `/api/jobs/{id}` | Forget a job (`?delete_file=true` also removes the file) |
| `GET` | `/api/jobs/{id}/file` | Download the finished file |
| `POST` | `/api/clear-finished` | Remove all finished jobs from the list |

Example:

```bash
curl -s localhost:8000/api/analyze \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ"}'

curl -s localhost:8000/api/jobs \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ","quality_id":"v1080"}'
```

---

## Project layout

```
app/
  config.py         Environment-driven settings
  models.py         Pydantic request schemas
  ytdlp_client.py   Probing, quality ladder, downloading, cancellation
  jobs.py           Job queue, concurrency limit, progress tracking
  main.py           FastAPI routes, SSE streaming, static hosting
  cli.py            Headless command-line downloader
  storage.py        Job history: MongoDB store with an in-memory fallback
  telegram_bot.py   Telegram bot front end (long polling)
  sources/
    http.py             HTTP helper with browser impersonation
    browser.py          Chromium session (Playwright) for JavaScript challenges
    mega.py             MEGA API client and AES-128-CTR decryption
    toonworld.py        Native toonworld4all.me extractor
    toonworld_hosts.py  Filepress, GDFlix, HubCloud, MEGA and AppDrive resolvers
    multimovies.py      Native multimovies.wtf extractor
    embed_resolver.py   Resolves the streaming providers used by multimovies
static/
  index.html        Single-page UI
  styles.css        Dark theme
  app.js            Front-end logic (SSE + polling fallback)
tests/
  test_api.py              Offline test suite (no network required)
  test_toonworld.py        Episode, season and token parsing
  test_toonworld_hosts.py  Host resolvers
  test_toonworld_resolution.py  Resolution pipeline and download checks
  test_multimovies.py      multimovies.wtf extractor
  test_storage.py          MongoDB / in-memory job history
  test_telegram_bot.py     Telegram command handling
  test_telegram_flow.py    Telegram keyboard and upload flow
```

---

## Continuous integration

`ci/github-actions-ci.yml` is a ready-made GitHub Actions workflow that runs
the test suite on every push. Copy it into place to enable it:

```bash
mkdir -p .github/workflows
cp ci/github-actions-ci.yml .github/workflows/ci.yml
```

(It ships in `ci/` because the GitHub API used to publish this repository
refuses to write anything under `.github/workflows`.)

---

## Tests

```bash
.venv/bin/pip install pytest httpx
.venv/bin/python -m pytest tests/ -q
```

The suite runs entirely offline — yt-dlp is stubbed out — so it never depends on
a live site being reachable. It currently reports **85 passed, 2 skipped** (the
skipped tests need `ffmpeg`/`ffprobe`).

---

## Telegram bot

The same engine, driven from Telegram. Set a token and the bot starts with the
web app - no second process, no webhook, no public URL required (it uses long
polling).

```bash
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwx ./run.sh
```

Talk to [@BotFather](https://t.me/BotFather) to get a token, then send a link:

| Command / action | What happens |
|---|---|
| `/start`, `/help` | Usage instructions |
| `/sites` | How many sites are supported |
| `/history` | Your recent downloads |
| `/cancel <job-id>` | Stop a running download |
| Send a link | The qualities appear as inline buttons |
| Pick a quality | The download starts and the message becomes a live progress bar |
| Finished | The file is uploaded back into the chat (video or document) |

For a season page the bot lists the episodes as buttons, so a whole season can
be pulled one episode at a time. Files larger than Telegram's 50 MB limit are
reported with a link to the web UI instead of being silently dropped.

**Locking it down**

```bash
TELEGRAM_BOT_TOKEN=... TELEGRAM_ALLOWED_CHATS=123456789,-1001234567890 ./run.sh
```

Only those chats may use the bot. Without the variable anyone who finds the bot
can use it.

---

## MongoDB job history

Running jobs always live in memory (so progress can stream at full speed), but
every job is also mirrored into MongoDB so the history survives a restart.

```bash
MONGO_URI=mongodb://localhost:27017 ./run.sh
```

| Variable | Default |
|---|---|
| `MONGO_URI` | *(empty - memory only)* |
| `MONGO_DB` | `universal_downloader` |
| `MONGO_COLLECTION` | `jobs` |

If MongoDB is unreachable the app logs a warning and falls back to in-memory
history - it never fails to start. The history is readable at
`GET /api/history` and from the `/history` Telegram command.

`docker compose up` starts a MongoDB alongside the downloader with the wiring
already done.

---

## Native support: toonworld4all.me

`toonworld4all.me` is handled natively instead of through the generic
extractor, because the site is a single-page application and its media links
are encrypted.

**What the extractor does**

1. A **season page** (`/naruto-shippuden-season-18-…`) is turned into a
   playlist of episode URLs on `archive.toonworld4all.me`.
2. An **episode page** is parsed from the `window.__PROPS__` JSON that the
   archive app server-renders, which yields the title, episode number, air
   date, runtime, poster, and one entry per quality tier:

   | Tier | Example label |
   |---|---|
   | `480p-h264-8bit` | 480p x264 — 113.23 MB |
   | `720p-h264-8bit` | 720p x264 — 198.41 MB |
   | `720p-h265-10bit` | 720p HEVC 10bit — 144.74 MB |
   | `1080p-h264-8bit` | 1080p x264 — 473.00 MB |
   | `1080p-h265-10bit` | 1080p HEVC 10bit — 302.86 MB |
   | `1080p-h265-hq-10bit` | 1080p HEVC 10bit [HQ] — 1.09 GB |

   Each tier carries the files offered for it on four hosts
   (HubCloud, Filepress, GDFlix, MEGA).
3. Every host is resolved through its **own resolver** and health-checked
   before the download starts. The downloader then tries each host in turn and
   reports precisely why a host was skipped.

**How the redirect tokens are decoded**

Each file link is an encrypted `/redirect/<token>`. The archive app decodes it
server-side and answers with an interstitial page that carries

* `link.domain` — the host the file lives on (`https://hubcloud.mov/drive/`),
* `link.hidden` — the file id on that host,
* `destination` — the ad shortener that guards the real links.

The interstitial is only shown to a visitor without a session cookie; a
second request with the same session is bounced to the shortener, so every
token is resolved with a *fresh* session.

**Host resolvers**

| Host | How the file is resolved |
|---|---|
| Filepress | `GET /api/file/get/<id>` returns the file description including `downloadUrl` |
| GDFlix | the server-rendered file page carries the Google Drive link, which yt-dlp downloads |
| HubCloud | the JavaScript challenge is answered, then `api/downloader.php` returns the file URL |
| MEGA | the file key is used with the MEGA API; the payload is decrypted with AES-128-CTR |
| AppDrive | the JavaScript redirect chain is followed to the real file |

**The shortener**

The ids the archive stores are placeholders: the hosts answer *"the file does
not exist"*. The real links are behind the ad shortener of the interstitial.
When that happens the downloader opens a **real Chromium session**
(Playwright), lets the shortener run its own JavaScript and picks up the host
links the shortener hands out. Verification widgets are never clicked; the
page is only given the chance to verify itself, and when it does not, the job
reports that instead of pretending the file was downloaded.

**The tokens rotate**

The archive mints a **new token on every page load** — three consecutive
fetches of the same episode return three different sets of tokens. A token
captured during the analysis is therefore worthless by the time the download
starts.

The app handles this by never downloading from the analysis snapshot. When a
job starts, the episode page is **fetched again** and the tokens from *that*
response are decoded, so the download always races against a live token.
`tests/test_toonworld.py` locks this behaviour in place: the test serves a
page with one token, analyses it, then serves the same page with a different
token and asserts that the download uses the new one.

**Cloudflare**

Both `toonworld4all.me` and `archive.toonworld4all.me` sit behind Cloudflare's
bot detection. The app uses `curl_cffi` to present a genuine Chrome TLS/HTTP
fingerprint, which clears the challenge without needing a browser.

**Placeholder protection**

A host that answers with a placeholder instead of the episode is detected:
the finished file is checked against `UVD_MIN_MEDIA_BYTES` (16 KiB by
default), against the `video-bytes` marker some hosts return, against the size
the archive announced for the tier, and with `ffprobe` so only a file with
real media streams passes. A placeholder is treated as a failed host, so the
download moves on to the next host instead of handing an 11 byte file to the
user.

**Browser settings**

| Variable | Meaning |
|---|---|
| `UVD_BROWSER_EXECUTABLE` | Path of the Chromium build, when it is not in a usual place |
| `UVD_BROWSER_HEADLESS` | `1` (default) runs the browser without a window, `0` shows it |
| `UVD_BROWSER_PROFILE` | Directory that keeps the cookies a challenge hands out |
| `UVD_BROWSER_TIMEOUT_MS` | How long the browser waits for a page |

The browser is optional: without it the downloader still resolves every host
that does not need a JavaScript challenge.

**When the shortener asks for a human check**

Some networks make the shortener show a Cloudflare checkbox that never clears
by itself. The downloader never clicks it. In that case the job ends with the
reason per host, for example:

```
Every download host failed for this episode. HubCloud: the host page did not
expose a direct file URL (the archive id looks like a placeholder) | Filepress:
Invaild File ID; the ad shortener asks for a Cloudflare verification that did
not complete on its own | ...
```

No file is written in that case. `/api/health` reports the Chromium build the
browser route would use (`browser`, `browser_path`).

---

## Native support: multimovies.wtf

`multimovies.wtf` is the public landing page; the catalogue itself lives on
`m1.multimovies.courses` (and `multinime.site` for anime). The app treats
all three as one source.

**What the extractor does**

1. A **series page** (`/series/<slug>-<id>`) becomes a playlist of episode
   URLs, in season/episode order.
2. An **episode page** (`/series/<slug>-<id>/season/3/episode/14`) and a
   **movie page** (`/movie/<slug>`) are parsed from the `watchConfig` object
   that the app server-renders. It lists every streaming provider offered for
   that title:

   | Provider | Embed URL |
   |---|---|
   | Cineverse | `rozgarlelo.modiplay.xyz/embed/tmdb/tv?id=652&s=3&e=14` |
   | GD mirror | `streams.iqsmartgames.com/embed/tv/652/3/14?key=…` |
   | Nexus | `www.moviesnexus.fun/tv/652/3/14` |
   | Vidrift | `embed.vidrift.net/embed/tv/652/3/14` |
   | VIDOUT | `vidout.pages.dev/tv/652/s3/e14` |
   | Vidbolt | `vidbolt.xyz/tv/652/3/14` |

3. Each provider is resolved to its media URL and the HLS master playlist is
   read to list the real qualities. Providers that cannot be resolved stay
   visible in the analysis with the reason, instead of disappearing.

**Resolving the providers**

Every provider hides its media URL behind its own JavaScript, so each one has
a dedicated resolver:

| Provider | How it is resolved |
|---|---|
| Cineverse | The embed only carries a player iframe; the proxied player document exposes the media URL as `var src="…"` |
| Vidrift | The page carries an `embedMeta.playbackToken`; `/api/source/…` exchanges it for a `proxyUrl` |
| VIDOUT | The episode maps to a `master.txt` in the GitHub-hosted manifest repository |
| Vidbolt | The page carries `window.__VB__`; `/api/resolve` returns the stream URL (the call needs a `Referer` header, otherwise it answers `403`) |
| Unknown | A headless Chromium fallback reads the player for providers without a resolver |

Two providers are deliberately left unresolved and are reported as such
instead of hanging the download:

| Provider | Why |
|---|---|
| GD mirror | The player hands out its source details encrypted and then runs a proof-of-work challenge behind a session path that changes on every load. The browser fallback reaches the player but cannot finish the challenge. |
| Nexus | The host answers with a Cloudflare "temporarily rate limited" page. |

**The `Referer` header**

The streaming hosts behind Cineverse and VIDOUT answer with a Cloudflare
block page ("Sorry, you have been blocked") unless the request carries the
`Referer` of the player that uses the playlist. The app therefore sends the
provider's embed URL as the `Referer` for both the playlist lookup and the
download itself — including every HLS segment request.

**Quality selection**

Qualities are merged across providers. If two providers offer 720p, both are
tried in order and the first one that delivers wins. Heights are rounded to
the standard tier, so a 1280x718 stream is listed as 720p instead of
getting a tier of its own:

```
720p  →  Cineverse, VIDOUT, Vidbolt
480p  →  VIDOUT
360p  →  Vidbolt
```

HLS streams are downloaded with yt-dlp's native downloader; a stream that the
native downloader cannot handle is retried with the ffmpeg downloader and
`--hls-use-mpegts` before the source is written off. Playlists that are
served as a `.txt` file are handed to ffmpeg straight away, because the native
downloader only recognises `.m3u8`. `pycryptodomex` is installed for the
AES-128 protected streams these hosts use.

Variant playlists that address their variants with a relative path are
resolved against the playlist URL, so the downloader never hands a bare
`/v/…` path to yt-dlp.

**Verified end to end**

`What's New, Scooby-Doo? S03E14 — E-Scream` was analysed and downloaded
through the app three times, once through a different provider each time:

| Quality | Provider | Result |
|---|---|---|
| 480p | Vidbolt | H.265 854×480, 21 min, 87.5 MB |
| 480p | VIDOUT | H.264 960×538, 19.6 min, 82.7 MB |
| 360p | Vidbolt | H.264 + AAC 640×360, 20.3 min, 79.4 MB |

All three were served back over `/api/jobs/<id>/file` as `video/mp4`.

The analysis resolves three of the six providers at once (Cineverse, VIDOUT
and Vidbolt, with Vidrift appearing intermittently) and reports the others
with the reason they were skipped.

---

## Troubleshooting

**"No downloader plugin recognises that URL."**
The site is not supported by this yt-dlp version, or the link is a landing page
rather than a media page. Upgrade with `pip install -U yt-dlp`, or open an issue
on [yt-dlp](https://github.com/yt-dlp/yt-dlp/issues).

**HTTP 403 / "unable to download webpage".**
The site wants a logged-in session or an age gate. Set `COOKIES_FROM_BROWSER=chrome`
and restart, or export cookies to a `cookies.txt` and point `COOKIES_FILE` at it.

**Videos download but have no sound.**
ffmpeg is missing or not on PATH. Merging video+audio requires it.

**Downloads fail on one site but work on others.**
Usually a rate limit or a geo-block. Try `PROXY=...` to route through a different
egress, and raise `REQUEST_TIMEOUT` for slow origins.

**Nothing is downloaded when I click Save to device.**
Check the browser's download settings; the file is also on disk under
`DOWNLOAD_DIR`, and the API endpoint `/api/jobs/{id}/file` serves it directly.

---

## Legal note

This tool downloads content that is publicly accessible. Whether you may keep,
share or re-publish what you download depends on the source's terms of service
and on copyright law in your jurisdiction. Download only material you have the
right to download — for example your own uploads, public-domain works, content
licensed for reuse, or media you have permission to archive. The authors
provide the tool; they do not grant you any rights to third-party content.

---

## Built on

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — the site support engine
- [FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/) — the API
- [ffmpeg](https://ffmpeg.org/) — merging and conversion
