from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .auth.router import router as auth_router
from .config import get_settings
from .routers import (actions, ai, architecture, events, governance, inventory,
                      meta, overview, people, security, settings as settings_router,
                      teams, technology)

settings = get_settings()
settings.validate_runtime()

app = FastAPI(title="MERIDIAN Engineering Platform", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API = "/api"
app.include_router(auth_router, prefix=API)
for r in (overview, inventory, events, actions, security, ai, architecture,
          technology, teams, people, governance, meta, settings_router):
    app.include_router(r.router, prefix=API)


@app.get("/api/health")
def health():
    return {"ok": True, "data_mode": settings.data_mode, "auth_mode": settings.auth_mode}


# ---- serve the built SPA in deployment -------------------------------------
# index.html must NEVER be cached: it names the content-hashed JS/CSS chunks, and
# a stale copy would point at chunks that no longer exist after a redeploy (→ blank
# pages). The hashed assets under /assets are immutable, so cache them hard.
dist = settings.frontend_dist
if dist and os.path.isdir(dist):
    dist_abs = os.path.abspath(dist)
    _INDEX = os.path.join(dist_abs, "index.html")
    _NO_STORE = {"Cache-Control": "no-cache, must-revalidate"}
    _IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}

    @app.get("/assets/{asset_path:path}")
    def assets(asset_path: str):
        candidate = os.path.normpath(os.path.join(dist_abs, "assets", asset_path))
        if candidate.startswith(os.path.join(dist_abs, "assets")) and os.path.isfile(candidate):
            return FileResponse(candidate, headers=_IMMUTABLE)
        # a missing hashed chunk means a stale client — 404 so the SW/loader recovers
        return FileResponse(_INDEX, status_code=404, headers=_NO_STORE)

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        candidate = os.path.normpath(os.path.join(dist_abs, full_path))
        if full_path and not full_path.startswith("api") and candidate.startswith(dist_abs) \
                and candidate != _INDEX and os.path.isfile(candidate):
            # other static files (favicon, etc.) — short cache
            return FileResponse(candidate, headers={"Cache-Control": "public, max-age=3600"})
        # every SPA route returns index.html, always revalidated
        return FileResponse(_INDEX, headers=_NO_STORE)
