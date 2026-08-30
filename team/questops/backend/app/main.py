from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import SessionLocal, init_db
from .routers import (access_routes, activity, ai, auth_routes, deps, dive,
                      game, insights, logging_routes, overview, projects_routes,
                      repos_routes, upgrades_routes, work)
from .seed import cleanup_demo_data, seed_demo

app = FastAPI(title=settings.app_name, docs_url="/api/docs", openapi_url="/api/openapi.json")

for router in (auth_routes.router, work.router, game.router,
               ai.router, insights.router,
               repos_routes.router, overview.router, upgrades_routes.router,
               dive.router, deps.router, access_routes.router,
               logging_routes.router, projects_routes.router,
               activity.router):
    app.include_router(router)


@app.middleware("http")
async def no_cache_spa(request: Request, call_next):
    """Never let a browser/proxy serve a stale SPA — the #1 cause of 'I
    deployed but the UI is old' (old app.js calling old routes). ETag-based
    revalidation stays cheap (304s), assets just can't be used blindly."""
    resp = await call_next(request)
    path = request.url.path
    if not path.startswith("/api/") and (
            path in ("/", "") or path.endswith((".html", ".js", ".css"))):
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


def _logo_path() -> Path | None:
    """Configured company logo, else the bundled demo logo in demo mode."""
    if settings.company_logo:
        cand = Path(settings.company_logo)
        if cand.is_file():
            return cand
    if settings.demo_mode:
        demo = _frontend_dir() / "demo-logo.png"
        if demo.is_file():
            return demo
    return None


@app.get("/api/branding")
def branding():
    return {"company_name": settings.company_name or ("Acme Retail" if settings.demo_mode else ""),
            "has_logo": _logo_path() is not None}


@app.get("/branding/logo")
def branding_logo():
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    path = _logo_path()
    if path is None:
        raise HTTPException(404, "no company logo configured")
    return FileResponse(path, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/health")
def health():
    return {"ok": True, "app": settings.app_name, "demo_mode": settings.demo_mode}


@app.on_event("startup")
def startup() -> None:
    init_db()
    db = SessionLocal()
    try:
        if settings.demo_mode:
            seed_demo(db)
        else:
            cleanup_demo_data(db)  # purge leftovers from any earlier demo run
    finally:
        db.close()


def _frontend_dir() -> Path:
    here = Path(__file__).resolve()
    for candidate in (here.parents[1] / "frontend",   # container: /app/app -> /app/frontend
                      here.parents[2] / "frontend"):  # repo: backend/app -> questops/frontend
        if candidate.is_dir():
            return candidate
    raise RuntimeError("frontend directory not found")


app.mount("/", StaticFiles(directory=_frontend_dir(), html=True), name="frontend")
