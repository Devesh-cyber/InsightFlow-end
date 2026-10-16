# InsightFlow API container. Same image runs locally, on Render, or on Cloud Run.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Memory tuning for a 512 MB instance (measured in Phase 1):
    # - Polars' allocator returns freed memory to the OS immediately instead of
    #   keeping it (after-job RSS 255 MB -> ~150 MB).
    # - Two compute threads: matches the small CPU share and caps buffer memory.
    _RJEM_MALLOC_CONF="background_thread:true,dirty_decay_ms:0,muzzy_decay_ms:0" \
    POLARS_MAX_THREADS=2

WORKDIR /app

# Install dependencies first so Docker can cache this layer between code changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# Run as a non-root user: if the app were ever compromised, it can't modify the image.
RUN useradd --create-home --uid 10001 appuser
USER appuser

# Render injects $PORT; default 8000 for local runs.
ENV PORT=8000
EXPOSE 8000

# `exec` makes uvicorn PID 1 so Ctrl+C / Render's stop signal reaches it
# directly (Phase 0 needed three Ctrl+C presses because sh swallowed it).
# One worker on purpose: 512 MB fits one Python process + data.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips='*' --timeout-graceful-shutdown 10"]
