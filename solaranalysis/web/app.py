from __future__ import annotations
import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from . import db, crypto
from .paths import Paths

log = logging.getLogger("solar.web")

CSRF_HEADER = "x-solar-csrf"


def db_dep_factory(paths: Paths):
    def _dep():
        conn = db.connect(paths.db_path)
        try:
            yield conn
        finally:
            conn.close()
    return _dep


def create_app(paths: Paths, run_manager=None, schedule_service=None) -> FastAPI:
    app = FastAPI()
    app.state.paths = paths
    # Also encrypts the stored plant portal credentials — see repo.create_plant.
    app.state.key = crypto.load_or_create_key(paths.key_path)
    app.state.run_manager = run_manager
    app.state.schedule_service = schedule_service
    app.state.db_dep = db_dep_factory(paths)

    conn = db.connect(paths.db_path)
    db.init_db(conn)
    conn.close()

    @app.middleware("http")
    async def csrf(request: Request, call_next):
        # The API is unauthenticated, which is exactly why this stays: requiring
        # a custom header forces a CORS preflight, so a page in someone's
        # browser can't drive-by POST /api/runs or DELETE /api/plants/3.
        if (request.url.path.startswith("/api/")
                and request.method in ("POST", "PUT", "DELETE")
                and request.headers.get(CSRF_HEADER) is None):
            return JSONResponse({"detail": "CSRF header required"}, status_code=403)
        return await call_next(request)

    from .routes.plants import router as plants_router
    from .routes.plant_history import router as plant_history_router
    from .routes.settings import router as settings_router
    app.include_router(plants_router, prefix="/api/plants")
    app.include_router(plant_history_router, prefix="/api/plants")
    app.include_router(settings_router, prefix="/api/settings")

    from .routes.schedules import router as schedules_router
    from .routes.imports import router as imports_router
    app.include_router(schedules_router, prefix="/api/schedules")
    app.include_router(imports_router, prefix="/api/import")

    from .routes.runs import router as runs_router
    app.include_router(runs_router, prefix="/api/runs")

    @app.on_event("startup")
    def _startup():
        if app.state.run_manager:
            app.state.run_manager.reconcile_on_startup()
        if app.state.schedule_service:
            app.state.schedule_service.start()

    dist = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                        "frontend", "dist")
    assets = os.path.join(dist, "assets")
    if os.path.isdir(assets):
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    _PLACEHOLDER = ("<!doctype html><meta charset='utf-8'>"
                    "<title>Solar Analysis</title>"
                    "<p>Frontend not built. Run <code>npm run build</code> in "
                    "<code>frontend/</code>.</p>")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "not found"}, status_code=404)
        index = os.path.join(dist, "index.html")
        if os.path.isfile(index):
            return FileResponse(index)
        return HTMLResponse(_PLACEHOLDER)

    return app
