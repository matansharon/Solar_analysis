from __future__ import annotations
import os
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import repo
from ..run_manager import Busy

router = APIRouter()
_RANGES = {"snapshot", "30d", "12mo", "all"}


class RunBody(BaseModel):
    time_range: str


def _conn(request: Request):
    yield from request.app.state.db_dep()


@router.get("")
def list_runs(limit: int = 50, offset: int = 0, conn=Depends(_conn)):
    return repo.list_runs(conn, limit=limit, offset=offset)


@router.post("")
def create_run(body: RunBody, request: Request):
    if body.time_range not in _RANGES:
        return JSONResponse({"detail": "invalid time_range"}, status_code=422)
    rm = request.app.state.run_manager
    try:
        rid = rm.start_run("manual", body.time_range)
    except Busy as b:
        return JSONResponse({"detail": "busy", "active": b.active}, status_code=409)
    return JSONResponse({"id": rid}, status_code=201)


@router.get("/{rid}")
def get_run(rid: int, request: Request, conn=Depends(_conn)):
    run = repo.get_run(conn, rid)
    if not run:
        return JSONResponse({"detail": "not found"}, status_code=404)
    rm = request.app.state.run_manager
    if run["status"] == "running" and rm:
        prog = rm.get_progress(rid)
        if prog:
            run["progress"] = prog
    return run


@router.post("/{rid}/cancel")
def cancel_run(rid: int, request: Request):
    rm = request.app.state.run_manager
    return {"cancelled": bool(rm and rm.cancel(rid))}


@router.get("/{rid}/log")
def run_log(rid: int, request: Request, conn=Depends(_conn)):
    run = repo.get_run(conn, rid)
    if not run:
        return JSONResponse({"detail": "not found"}, status_code=404)
    path = os.path.join(request.app.state.paths.data_dir, run["log_path"])
    text = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    return {"log": text}
