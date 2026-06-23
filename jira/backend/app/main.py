"""Trackly API application entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.core.config import settings

logging.basicConfig(level=logging.INFO if not settings.debug else logging.DEBUG)
log = logging.getLogger("trackly")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run first-boot bootstrap (create tables + seed defaults + admin user).
    from app.core.bootstrap import run_bootstrap

    try:
        run_bootstrap()
        log.info("Bootstrap complete")
    except Exception:  # pragma: no cover - surfaced in logs, container retries
        log.exception("Bootstrap failed")
        raise
    yield


app = FastAPI(
    title=f"{settings.app_name} API",
    version="1.0.0",
    description="An open issue & project tracker. Original implementation.",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/api/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "env": settings.app_env}


@app.get("/health")
def health_root() -> dict:
    return {"status": "ok"}
