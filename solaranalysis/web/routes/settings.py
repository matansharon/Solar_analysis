from __future__ import annotations
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from .. import repo

router = APIRouter()


class SettingsBody(BaseModel):
    model: str | None = None
    max_input_tokens: int = 60000
    output_language: str = "en"


def _conn(request: Request):
    yield from request.app.state.db_dep()


@router.get("")
def get_settings(conn=Depends(_conn)):
    return repo.get_app_settings(conn)


@router.put("")
def put_settings(body: SettingsBody, request: Request, conn=Depends(_conn)):
    repo.set_app_settings(conn, body.model, body.max_input_tokens, body.output_language)
    return {"ok": True}
