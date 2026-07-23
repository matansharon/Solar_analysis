from __future__ import annotations
import os
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import db, repo
from ..run_manager import Busy

router = APIRouter()
_RANGES = {"snapshot", "30d", "12mo", "all"}


class RunBody(BaseModel):
    time_range: str
    plant_id: int | None = None


def _conn(request: Request):
    yield from request.app.state.db_dep()


@router.get("")
def list_runs(limit: int = 50, offset: int = 0, conn=Depends(_conn)):
    return repo.list_runs(conn, limit=limit, offset=offset)


@router.post("")
def create_run(body: RunBody, request: Request, conn=Depends(_conn)):
    if body.time_range not in _RANGES:
        return JSONResponse({"detail": "invalid time_range"}, status_code=422)
    if body.plant_id is not None:
        p = repo.get_plant(conn, body.plant_id)
        if not p:
            return JSONResponse({"detail": "system not found"}, status_code=422)
        if not p["enabled"]:
            return JSONResponse({"detail": "system is disabled"}, status_code=422)
    rm = request.app.state.run_manager
    try:
        rid = rm.start_run("manual", body.time_range, plant_id=body.plant_id)
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


import asyncio as _asyncio
import json as _json
import queue as _queue
from fastapi.responses import StreamingResponse, Response


@router.get("/{rid}/stream")
async def stream_run(rid: int, request: Request):
    rm = request.app.state.run_manager
    conn = db.connect(request.app.state.paths.db_path)
    try:
        exists = repo.get_run(conn, rid) is not None
    finally:
        conn.close()
    if not exists:
        return JSONResponse({"detail": "not found"}, status_code=404)

    async def gen():
        if not rm:
            return
        q = rm.subscribe(rid)
        try:
            idle = 0
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = q.get_nowait()
                except _queue.Empty:
                    idle += 1
                    if idle >= 120:  # ~30s of idle at 0.25s/poll
                        idle = 0
                        yield ": keepalive\n\n"
                    await _asyncio.sleep(0.25)
                    continue
                idle = 0
                yield f"data: {_json.dumps(msg)}\n\n"
                if msg.get("type") == "end":
                    break
        finally:
            rm.unsubscribe(rid, q)

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/{rid}/report")
def run_report(rid: int, request: Request, conn=Depends(_conn)):
    run = repo.get_run(conn, rid)
    if not run or not run["report_path"]:
        return JSONResponse({"detail": "not found"}, status_code=404)
    paths = request.app.state.paths
    full = os.path.realpath(os.path.join(paths.data_dir, run["report_path"]))
    out_root = os.path.realpath(paths.output_dir)
    if not full.startswith(out_root + os.sep) or not os.path.isfile(full):
        return JSONResponse({"detail": "not found"}, status_code=404)
    with open(full, encoding="utf-8", errors="replace") as f:
        html = f.read()
    return Response(content=html, media_type="text/html", headers={
        "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'",
        "X-Content-Type-Options": "nosniff",
    })
