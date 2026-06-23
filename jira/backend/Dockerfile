# Trackly backend — FastAPI served by uvicorn.
#
# psycopg[binary] and bcrypt ship manylinux wheels, so a normal build needs no
# compiler. We keep a tiny, single-stage image. If you ever swap to
# psycopg (source) you can uncomment the build-essentials block below.
FROM python:3.12-slim

# Faster, cleaner Python in containers.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# --- (optional) build deps, only if a wheel is unavailable for your platform ---
# RUN apt-get update \
#     && apt-get install -y --no-install-recommends build-essential libpq-dev \
#     && rm -rf /var/lib/apt/lists/*

# curl is used by the container HEALTHCHECK.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first so this layer is cached across app code changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application source.
COPY . .

# Attachments live on a mounted volume; create the dir and hand it to a
# non-root runtime user.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/attachments \
    && chown -R appuser:appuser /app /data
USER appuser

EXPOSE 8000

# Container-level healthcheck hitting the public liveness endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
