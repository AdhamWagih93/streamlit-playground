from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import SessionLocal, init_db
from .routers import (access_routes, activity, ai, auth_routes, configs_routes, deps, dive,
                      game, insights, logging_routes, overview, projects_routes,
                      repos_routes, upgrades_routes, work)
from .seed import cleanup_demo_data, seed_demo

app = FastAPI(title=settings.app_name, docs_url="/api/docs", openapi_url="/api/openapi.json")
# big JSON (the Projects report can carry tens of thousands of events) —
# compress anything over 1 KB; browsers decode transparently
app.add_middleware(GZipMiddleware, minimum_size=1024)

for router in (auth_routes.router, work.router, game.router,
               ai.router, insights.router,
               repos_routes.router, overview.router, upgrades_routes.router,
               dive.router, deps.router, access_routes.router,
               logging_routes.router, projects_routes.router,
               activity.router, configs_routes.router):
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


_LOGO_EXT = {".png": "image/png", ".svg": "image/svg+xml", ".jpg": "image/jpeg",
             ".jpeg": "image/jpeg", ".webp": "image/webp"}


def _logo_candidates() -> list[Path]:
    """Where a company logo may live, in priority order: the configured path
    (absolute, or relative to the questops folder / the container's /app),
    then the always-mounted branding folder(s), then the demo mark."""
    here = Path(__file__).resolve()
    roots = [here.parents[1], here.parents[2] if len(here.parents) > 2 else here.parents[1]]
    cands: list[Path] = []
    if settings.company_logo:
        raw = Path(settings.company_logo)
        cands.append(raw)
        if not raw.is_absolute():
            cands += [r / raw for r in roots]
    for r in roots:
        for ext in _LOGO_EXT:
            cands.append(r / "branding" / f"logo{ext}")
    if settings.demo_mode:
        cands.append(_frontend_dir() / "demo-logo.png")
    seen, out = set(), []
    for c in cands:
        if str(c) not in seen:
            seen.add(str(c))
            out.append(c)
    return out


def _logo_path() -> Path | None:
    for c in _logo_candidates():
        if c.is_file() and c.suffix.lower() in _LOGO_EXT:
            return c
    return None


@app.get("/api/branding")
def branding():
    path = _logo_path()
    return {"company_name": settings.company_name or ("Acme Retail" if settings.demo_mode else ""),
            "has_logo": path is not None,
            "logo": str(path) if path else None,
            "configured": settings.company_logo or None,
            "checked": [{"path": str(c), "exists": c.is_file()} for c in _logo_candidates()]}


@app.get("/branding/logo")
def branding_logo():
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    path = _logo_path()
    if path is None:
        raise HTTPException(404, "no company logo found — put it at branding/logo.png "
                                 "(see /api/branding for the paths checked)")
    return FileResponse(path, media_type=_LOGO_EXT[path.suffix.lower()],
                        headers={"Cache-Control": "no-cache"})


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
    from .integrations import archconfig
    archconfig.warm_up()   # Configurations page caches, built off the request path


def _frontend_dir() -> Path:
    here = Path(__file__).resolve()
    for candidate in (here.parents[1] / "frontend",   # container: /app/app -> /app/frontend
                      here.parents[2] / "frontend"):  # repo: backend/app -> questops/frontend
        if candidate.is_dir():
            return candidate
    raise RuntimeError("frontend directory not found")


app.mount("/", StaticFiles(directory=_frontend_dir(), html=True), name="frontend")
