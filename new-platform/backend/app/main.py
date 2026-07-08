from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .auth.router import router as auth_router
from .config import get_settings
from .routers import (actions, ai, architecture, events, governance, inventory,
                      meta, overview, people, security, teams, technology)

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
          technology, teams, people, governance, meta):
    app.include_router(r.router, prefix=API)


@app.get("/api/health")
def health():
    return {"ok": True, "data_mode": settings.data_mode, "auth_mode": settings.auth_mode}


# ---- serve the built SPA in deployment -------------------------------------
dist = settings.frontend_dist
if dist and os.path.isdir(dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(dist, "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        candidate = os.path.normpath(os.path.join(dist, full_path))
        if full_path and not full_path.startswith("api") and candidate.startswith(os.path.abspath(dist)) \
                and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(dist, "index.html"))
