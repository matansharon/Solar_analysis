from __future__ import annotations
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from .. import repo
from ...core import measurements

router = APIRouter()


def _conn(request: Request):
    yield from request.app.state.db_dep()


@router.get("/{pid}/devices")
def plant_devices(pid: int, conn=Depends(_conn)):
    if not repo.get_plant(conn, pid):
        return JSONResponse({"detail": "not found"}, status_code=404)
    return measurements.load_devices_latest(conn, pid)


@router.get("/{pid}/alerts")
def plant_alerts(pid: int, limit: int = 100, conn=Depends(_conn)):
    if not repo.get_plant(conn, pid):
        return JSONResponse({"detail": "not found"}, status_code=404)
    return measurements.load_alerts(conn, pid, limit=limit)


# No adapter currently populates PlantData.power_timeseries, so this endpoint
# always returns an empty list on real runs today (infrastructure for future adapter work).
@router.get("/{pid}/power")
def plant_power(pid: int, since: str | None = None, conn=Depends(_conn)):
    if not repo.get_plant(conn, pid):
        return JSONResponse({"detail": "not found"}, status_code=404)
    points = measurements.load_power_series(conn, pid, since=since)
    return [{"timestamp_local": p.timestamp_local, "power_kw": p.power_kw} for p in points]


@router.get("/{pid}/energy")
def plant_energy(pid: int, granularity: str = "day", since: str | None = None, conn=Depends(_conn)):
    if not repo.get_plant(conn, pid):
        return JSONResponse({"detail": "not found"}, status_code=404)
    points = measurements.load_energy_series(conn, pid, granularity=granularity, since=since)
    return [{"timestamp_local": p.timestamp_local, "energy_kwh": p.energy_kwh} for p in points]
