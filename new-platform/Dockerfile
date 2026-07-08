# ---- frontend build ----------------------------------------------------
FROM node:20-alpine AS webbuild
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- backend runtime -----------------------------------------------------
FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONUNBUFFERED=1 \
    RUNNING_IN_CONTAINER=true \
    FRONTEND_DIST=/srv/static \
    DATA_MODE=live \
    AUTH_MODE=entra

COPY backend/requirements.txt backend/requirements-live.txt ./
RUN pip install --no-cache-dir -r requirements-live.txt

COPY backend/app ./app
COPY --from=webbuild /web/dist ./static

# non-root: required by the Helm chart's pod securityContext (runAsNonRoot)
RUN useradd --system --uid 10001 --no-create-home meridian \
    && mkdir -p /tmp/meridian-repos && chown -R meridian /tmp/meridian-repos
USER 10001

EXPOSE 8000
# No secrets in this image — provide them via env / mounted files at deploy time.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
