from __future__ import annotations
import hashlib
import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.routing import Match

from . import db, repo, crypto, auth as authmod
from .paths import Paths

log = logging.getLogger("solar.web")

COOKIE = "solar_session"
_PUBLIC = {"/api/auth/status", "/api/auth/login", "/api/auth/setup"}


def db_dep_factory(paths: Paths):
    def _dep():
        conn = db.connect(paths.db_path)
        try:
            yield conn
        finally:
            conn.close()
    return _dep


def _authenticated(request: Request) -> bool:
    paths: Paths = request.app.state.paths
    cookie = request.cookies.get(COOKIE)
    if not cookie:
        return False
    conn = db.connect(paths.db_path)
    try:
        epoch = repo.get_session_epoch(conn)
    finally:
        conn.close()
    return authmod.check_cookie(request.app.state.key, cookie, epoch)


def _matches_known_route(request: Request) -> bool:
    """True if some registered route (other than the SPA catch-all) matches
    this request's path, regardless of HTTP method. Lets the auth middleware
    tell "real API endpoint, unauthenticated" (401) apart from "no such
    endpoint" (fall through to the catch-all's JSON 404)."""
    for route in request.app.router.routes:
        if getattr(route, "path", None) == "/{full_path:path}":
            continue
        match, _ = route.matches(request.scope)
        if match != Match.NONE:
            return True
    return False


def create_app(paths: Paths, run_manager=None, schedule_service=None) -> FastAPI:
    app = FastAPI()
    app.state.paths = paths
    app.state.key = crypto.load_or_create_key(paths.key_path)
    app.state.rate_limiter = authmod.RateLimiter(max_fails=5, window_s=60)
    app.state.run_manager = run_manager
    app.state.schedule_service = schedule_service
    app.state.db_dep = db_dep_factory(paths)

    # First-boot: generate + log a setup token if none exists yet.
    conn = db.connect(paths.db_path)
    db.init_db(conn)
    if repo.setup_required(conn) and repo.get_setup_token_hash(conn) is None:
        token = os.urandom(16).hex()
        repo.set_setup_token_hash(conn, hashlib.sha256(token.encode()).hexdigest())
        log.warning("SETUP TOKEN (enter in the web setup screen): %s", token)
    conn.close()

    @app.middleware("http")
    async def auth_and_csrf(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/"):
            if request.method in ("POST", "PUT", "DELETE"):
                if request.headers.get(authmod.CSRF_HEADER) is None:
                    return JSONResponse({"detail": "CSRF header required"}, status_code=403)
            if path not in _PUBLIC and not _authenticated(request):
                if _matches_known_route(request):
                    return JSONResponse({"detail": "authentication required"}, status_code=401)
                # No real endpoint at this path: fall through so the SPA
                # catch-all can return a plain JSON 404 instead of leaking
                # a 401 for paths that don't exist.
        return await call_next(request)

    from .routes.auth import router as auth_router
    app.include_router(auth_router, prefix="/api/auth")

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
