from __future__ import annotations
import os
import re
from dataclasses import dataclass, field
from dotenv import load_dotenv
import yaml

_ENV_REF = re.compile(r"\$\{([A-Z0-9_]+)\}")

@dataclass
class AuthConfig:
    platform: str
    mode: str = "password"
    username: str | None = None
    password: str | None = None
    token: str | None = None

@dataclass
class PlantConfig:
    name: str
    auth: AuthConfig
    tariff_per_kwh: float | None = None
    currency: str | None = None

@dataclass
class AppConfig:
    plants: list[PlantConfig] = field(default_factory=list)
    model: str | None = None
    max_input_tokens: int = 60000
    output_language: str = "en"

def _resolve(value):
    if not isinstance(value, str):
        return value
    def repl(m):
        name = m.group(1)
        val = os.environ.get(name)
        if val is None:
            raise ValueError(f"Environment variable {name} is not set (referenced in config)")
        return val
    return _ENV_REF.sub(repl, value)

def load_config(config_path: str, env_path: str | None = None) -> AppConfig:
    load_dotenv(env_path)  # loads .env from cwd if env_path is None
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    plants = []
    for p in raw.get("plants", []):
        a = p["auth"]
        auth = AuthConfig(
            platform=a["platform"],
            mode=a.get("mode", "password"),
            username=_resolve(a.get("username")),
            password=_resolve(a.get("password")),
            token=_resolve(a.get("token")),
        )
        plants.append(PlantConfig(
            name=p["name"], auth=auth,
            tariff_per_kwh=p.get("tariff_per_kwh"),
            currency=p.get("currency"),
        ))
    return AppConfig(
        plants=plants,
        model=raw.get("model"),
        max_input_tokens=raw.get("max_input_tokens", 60000),
        output_language=raw.get("output_language", "en"),
    )
