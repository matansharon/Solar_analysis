from __future__ import annotations
import re
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


_TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
_TIME_RANGES = {"snapshot", "30d", "12mo", "all"}


def validate_schedule(data: dict, is_create: bool) -> str | None:
    """Return an error message if `data` is invalid, else None.

    On create, the three schedule fields are required (presence is checked
    by the caller); here we only validate the format/values of whichever
    fields are present. On update, only fields present in the payload are
    validated.
    """
    if "time_of_day" in data:
        if not isinstance(data["time_of_day"], str) or not _TIME_RE.match(data["time_of_day"]):
            return "time_of_day must be HH:MM (24h)"
    if "days_of_week" in data:
        days = data["days_of_week"]
        if not isinstance(days, str) or not days.strip():
            return "days_of_week must be a non-empty comma-separated list of integers 0-6"
        parts = [p.strip() for p in days.split(",")]
        for p in parts:
            if not p.isdigit() or not (0 <= int(p) <= 6):
                return "days_of_week must be a non-empty comma-separated list of integers 0-6"
    if "time_range" in data:
        if data["time_range"] not in _TIME_RANGES:
            return "time_range must be one of: " + ", ".join(sorted(_TIME_RANGES))
    return None


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
    err = validate_schedule(data, is_create=True)
    if err:
        return JSONResponse({"detail": err}, status_code=422)
    sid = repo.create_schedule(conn, data)
    _reload(request)
    return JSONResponse({"id": sid}, status_code=201)


@router.put("/{sid}")
def update_schedule(sid: int, body: ScheduleBody, request: Request, conn=Depends(_conn)):
    data = body.model_dump(exclude_unset=True)
    err = validate_schedule(data, is_create=False)
    if err:
        return JSONResponse({"detail": err}, status_code=422)
    try:
        repo.update_schedule(conn, sid, data)
    except KeyError:
        return JSONResponse({"detail": "not found"}, status_code=404)
    _reload(request)
    return {"ok": True}


@router.delete("/{sid}")
def delete_schedule(sid: int, request: Request, conn=Depends(_conn)):
    repo.delete_schedule(conn, sid)
    _reload(request)
    return {"ok": True}
