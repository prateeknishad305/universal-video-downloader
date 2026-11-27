FROM python:3.12-slim

ARG INSTALL_BROWSER=0

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    DATA_DIR=/data \
    PYTHONMALLOC=malloc \
    MALLOC_ARENA_MAX=2 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    UVD_BROWSER_ENABLED=0 \
    MAX_CONCURRENT_JOBS=1 \
    UVD_MAX_FILE_MB=2048 \
    UVD_MAX_HISTORY_JOBS=200 \
    UVD_MIN_FREE_DISK_MB=512 \
    UVD_FFMPEG_THREADS=2

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates \
      ffmpeg \
      curl \
      dumb-init \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements-browser.txt ./
RUN pip install --no-cache-dir -r requirements.txt

RUN if [ "$INSTALL_BROWSER" = "1" ]; then \
      pip install --no-cache-dir -r requirements-browser.txt \
      && python -m playwright install --with-deps chromium; \
    else \
      echo "Chromium is not installed; set UVD_BROWSER_ENABLED=1 only with INSTALL_BROWSER=1."; \
    fi

COPY app ./app
COPY static ./static
COPY run.sh .

RUN mkdir -p /data/downloads \
 && chmod +x run.sh \
 && useradd --create-home --uid 10001 appuser \
 && chown -R appuser:appuser /app /data

USER appuser

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/api/health').read()" || exit 1

CMD ["dumb-init", "--", "sh", "-c", "exec uvicorn app.main:app --host $HOST --port $PORT --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 30 --no-server-header"]
