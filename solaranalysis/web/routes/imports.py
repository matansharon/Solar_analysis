from __future__ import annotations
import os
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from .. import importer

router = APIRouter()


def _conn(request: Request):
    yield from request.app.state.db_dep()


@router.post("")
def run_import(request: Request, conn=Depends(_conn)):
    paths = request.app.state.paths
    if not os.path.exists(paths.config_yaml):
        return JSONResponse({"detail": "config.yaml not found"}, status_code=404)
    summary = importer.import_config(conn, request.app.state.key,
                                     paths.config_yaml, paths.env_file)
    if summary["error"]:
        return JSONResponse({**summary, "detail": summary["error"]}, status_code=400)
    return summary
