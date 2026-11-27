#!/usr/bin/env bash
# Launch the Universal Video Downloader.
#
#   ./run.sh              -> start on http://127.0.0.1:8000
#   HOST=0.0.0.0 ./run.sh -> expose on the LAN
#   PORT=9000 ./run.sh    -> use a different port
#
# Set UVD_BROWSER_ENABLED=0 to run without the browser route.  That keeps the
# install small and the memory low, which is what a small host wants.
set -euo pipefail

cd "$(dirname "$0")"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
VENV="${VENV:-.venv}"
PY="$VENV/bin/python"
BROWSER_ENABLED="${UVD_BROWSER_ENABLED:-1}"

if [ ! -x "$PY" ]; then
  echo "==> Creating virtual environment in $VENV"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --upgrade pip
  echo "==> Installing dependencies"
  "$VENV/bin/pip" install -r requirements.txt
fi

if [ "$BROWSER_ENABLED" != "0" ] && ! "$PY" -c "import playwright" >/dev/null 2>&1; then
  echo "==> Installing the browser route (Playwright)"
  "$VENV/bin/pip" install -r requirements-browser.txt
  "$VENV/bin/python" -m playwright install chromium
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "WARNING: ffmpeg was not found on PATH."
  echo "         Video+audio merging and MP3 extraction will not work."
  echo "         macOS: brew install ffmpeg | Debian/Ubuntu: apt install ffmpeg"
fi

if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
  echo "==> Telegram bot enabled (poll mode)"
fi
if [ -n "${MONGO_URI:-}" ]; then
  echo "==> MongoDB job history enabled"
fi

echo "==> Universal Downloader running on http://$HOST:$PORT"
exec "$VENV/bin/uvicorn" app.main:app \
  --host "$HOST" \
  --port "$PORT" \
  --proxy-headers \
  --forwarded-allow-ips='*' \
  --timeout-keep-alive 30 \
  --no-server-header \
  "$@"
