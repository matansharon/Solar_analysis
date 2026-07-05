from __future__ import annotations
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import repo

router = APIRouter()


class ScheduleBody(BaseModel):
    time_of_day: str | None = None
    days_of_week: str | None = None
    time_range: str | None = None
    enabled: bool | None = None


def _conn(request: Request):
    yield from request.app.state.db_dep()


def _reload(request: Request):
    svc = request.app.state.schedule_service
    if svc:
        svc.reload()


@router.get("")
def list_schedules(conn=Depends(_conn)):
    return repo.list_schedules(conn)


@router.post("")
def create_schedule(body: ScheduleBody, request: Request, conn=Depends(_conn)):
    data = body.model_dump(exclude_none=True)
    for req in ("time_of_day", "days_of_week", "time_range"):
        if req not in data:
            return JSONResponse({"detail": f"{req} required"}, status_code=422)
    sid = repo.create_schedule(conn, data)
    _reload(request)
    return JSONResponse({"id": sid}, status_code=201)


@router.put("/{sid}")
def update_schedule(sid: int, body: ScheduleBody, request: Request, conn=Depends(_conn)):
    try:
        repo.update_schedule(conn, sid, body.model_dump(exclude_unset=True))
    except KeyError:
        return JSONResponse({"detail": "not found"}, status_code=404)
    _reload(request)
    return {"ok": True}


@router.delete("/{sid}")
def delete_schedule(sid: int, request: Request, conn=Depends(_conn)):
    repo.delete_schedule(conn, sid)
    _reload(request)
    return {"ok": True}
