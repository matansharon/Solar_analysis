from __future__ import annotations
from . import repo
from ..config import load_config


def import_config(conn, key, config_yaml: str, env_file: str) -> dict:
    summary = {"created": [], "updated": [], "secrets": {}, "settings": {}, "error": None}
    try:
        cfg = load_config(config_yaml, env_file)
    except Exception as e:
        summary["error"] = str(e)
        return summary
    existing = {p["name"]: p["id"] for p in repo.list_plants(conn)}
    for pc in cfg.plants:
        data = {"name": pc.name, "platform": pc.auth.platform,
                "auth_mode": pc.auth.mode, "username": pc.auth.username,
                "password": pc.auth.password, "token": pc.auth.token,
                "tariff_per_kwh": pc.tariff_per_kwh, "currency": pc.currency}
        if pc.name in existing:
            repo.update_plant(conn, key, existing[pc.name], data)
            summary["updated"].append(pc.name)
        else:
            repo.create_plant(conn, key, data)
            summary["created"].append(pc.name)
        summary["secrets"][pc.name] = {"password": bool(pc.auth.password),
                                       "token": bool(pc.auth.token)}
    repo.set_app_settings(conn, cfg.model, cfg.max_input_tokens, cfg.output_language)
    summary["settings"] = {"model": cfg.model,
                           "max_input_tokens": cfg.max_input_tokens,
                           "output_language": cfg.output_language}
    return summary
