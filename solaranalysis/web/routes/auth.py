from __future__ import annotations
import hashlib
import hmac

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import repo, auth as authmod
from ..app import COOKIE

router = APIRouter()


class SetupBody(BaseModel):
    token: str
    password: str


class LoginBody(BaseModel):
    password: str


class PasswordBody(BaseModel):
    old: str
    new: str


def _conn(request: Request):
    return request.app.state.db_dep


def _set_cookie(resp: Response, request: Request, conn):
    epoch = repo.get_session_epoch(conn)
    cookie = authmod.make_cookie(request.app.state.key, epoch)
    resp.set_cookie(COOKIE, cookie, httponly=True, samesite="lax",
                    max_age=30 * 24 * 3600)


@router.get("/status")
def status(request: Request, conn=Depends(lambda r=None: None)):
    p = request.app.state.paths
    from .. import db
    c = db.connect(p.db_path)
    try:
        setup_required = repo.setup_required(c)
    finally:
        c.close()
    from ..app import _authenticated
    return {"setup_required": setup_required, "authenticated": _authenticated(request)}


@router.post("/setup")
def setup(body: SetupBody, request: Request):
    p = request.app.state.paths
    from .. import db
    c = db.connect(p.db_path)
    try:
        if not repo.setup_required(c):
            return JSONResponse({"detail": "already set up"}, status_code=409)
        stored = repo.get_setup_token_hash(c)
        given = hashlib.sha256(body.token.encode()).hexdigest()
        if not stored or not hmac.compare_digest(stored, given):
            return JSONResponse({"detail": "invalid setup token"}, status_code=403)
        repo.set_password_hash(c, authmod.hash_password(body.password))
        repo.clear_setup_token(c)
        resp = JSONResponse({"ok": True})
        _set_cookie(resp, request, c)
        return resp
    finally:
        c.close()


@router.post("/login")
def login(body: LoginBody, request: Request):
    ip = request.client.host if request.client else "?"
    rl = request.app.state.rate_limiter
    if rl.is_blocked(ip):
        return JSONResponse({"detail": "too many attempts"}, status_code=429)
    p = request.app.state.paths
    from .. import db
    c = db.connect(p.db_path)
    try:
        h = repo.get_password_hash(c)
        if not h or not authmod.verify_password(body.password, h):
            rl.record_failure(ip)
            return JSONResponse({"detail": "invalid password"}, status_code=401)
        rl.reset(ip)
        resp = JSONResponse({"ok": True})
        _set_cookie(resp, request, c)
        return resp
    finally:
        c.close()


@router.post("/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp


@router.put("/password")
def change_password(body: PasswordBody, request: Request):
    p = request.app.state.paths
    from .. import db
    c = db.connect(p.db_path)
    try:
        h = repo.get_password_hash(c)
        if not h or not authmod.verify_password(body.old, h):
            return JSONResponse({"detail": "wrong current password"}, status_code=403)
        repo.set_password_hash(c, authmod.hash_password(body.new))
        repo.bump_session_epoch(c)  # invalidate all existing cookies, including the caller's
        return JSONResponse({"ok": True})
    finally:
        c.close()
