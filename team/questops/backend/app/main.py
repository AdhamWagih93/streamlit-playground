from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import SessionLocal, init_db
from .routers import (actions, ai, auth_routes, game, insights, prompts,
                      repos_routes, work)
from .seed import cleanup_demo_data, seed_demo

app = FastAPI(title=settings.app_name, docs_url="/api/docs", openapi_url="/api/openapi.json")

for router in (auth_routes.router, work.router, game.router,
               prompts.router, actions.router, ai.router, insights.router,
               repos_routes.router):
    app.include_router(router)


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
