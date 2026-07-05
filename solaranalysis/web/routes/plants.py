from __future__ import annotations
import sqlite3
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import repo, db

router = APIRouter()

_PLATFORMS = {"solaredge", "growatt", "sma"}


class PlantBody(BaseModel):
    name: str | None = None
    platform: str | None = None
    auth_mode: str | None = None
    username: str | None = None
    password: str | None = None
    token: str | None = None
    tariff_per_kwh: float | None = None
    currency: str | None = None
    enabled: bool | None = None


def validate_plant(data: dict, existing: dict | None) -> None:
    is_create = existing is None
    platform = data.get("platform") or (existing or {}).get("platform")
    if data.get("auth_mode") == "token" and platform != "growatt":
        raise ValueError("token mode is only valid for growatt")
    if platform != "growatt":
        auth_mode = "password"
    else:
        auth_mode = data.get("auth_mode") or (existing or {}).get("auth_mode") or "password"
    if platform not in _PLATFORMS:
        raise ValueError(f"platform must be one of {sorted(_PLATFORMS)}")
    if is_create and not (data.get("name") or "").strip():
        raise ValueError("name is required")
    if not is_create and "name" in data and not (data.get("name") or "").strip():
        raise ValueError("name cannot be empty")
    if is_create:
        if auth_mode == "password" and not (data.get("username") and data.get("password")):
            raise ValueError("password mode requires username and password")
        if auth_mode == "token" and not data.get("token"):
            raise ValueError("token mode requires a token")
    else:
        has_pw = existing["has_password"] or bool(data.get("password"))
        has_tok = existing["has_token"] or bool(data.get("token"))
        if auth_mode == "password" and not (
                (data.get("username") or existing["username"]) and has_pw):
            raise ValueError("password mode requires username and a stored/new password")
        if auth_mode == "token" and platform == "growatt" and not has_tok:
            raise ValueError("token mode requires a stored/new token")


def _conn(request: Request):
    yield from request.app.state.db_dep()


@router.get("")
def list_plants(conn=Depends(_conn)):
    return repo.list_plants(conn)


@router.post("")
def create_plant(body: PlantBody, request: Request, conn=Depends(_conn)):
    data = body.model_dump(exclude_none=True)
    try:
        validate_plant(data, None)
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=422)
    try:
        pid = repo.create_plant(conn, request.app.state.key, data)
    except sqlite3.IntegrityError:
        return JSONResponse({"detail": "a plant with that name already exists"}, status_code=422)
    return JSONResponse({"id": pid}, status_code=201)


@router.get("/{pid}")
def get_plant(pid: int, conn=Depends(_conn)):
    p = repo.get_plant(conn, pid)
    if not p:
        return JSONResponse({"detail": "not found"}, status_code=404)
    return p


@router.put("/{pid}")
def update_plant(pid: int, body: PlantBody, request: Request, conn=Depends(_conn)):
    existing = repo.get_plant(conn, pid)
    if not existing:
        return JSONResponse({"detail": "not found"}, status_code=404)
    data = body.model_dump(exclude_unset=True)
    try:
        validate_plant(data, existing)
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=422)
    try:
        repo.update_plant(conn, request.app.state.key, pid, data)
    except sqlite3.IntegrityError:
        return JSONResponse({"detail": "a plant with that name already exists"}, status_code=422)
    return {"ok": True}


@router.delete("/{pid}")
def delete_plant(pid: int, conn=Depends(_conn)):
    repo.delete_plant(conn, pid)
    return {"ok": True}


@router.post("/{pid}/test")
def test_plant(pid: int, request: Request, conn=Depends(_conn)):
    p = repo.get_plant(conn, pid)
    if not p:
        return JSONResponse({"detail": "not found"}, status_code=404)
    if not p["enabled"]:
        return JSONResponse({"detail": "plant is disabled"}, status_code=409)
    rm = request.app.state.run_manager
    from ..run_manager import Busy
    try:
        return rm.run_test(pid)
    except Busy as b:
        return JSONResponse({"detail": "busy", "active": b.active}, status_code=409)
