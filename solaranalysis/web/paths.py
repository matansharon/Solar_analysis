from __future__ import annotations
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Paths:
    data_dir: str
    app_dir: str

    @classmethod
    def create(cls, data_dir: str, app_dir: str) -> "Paths":
        p = cls(os.path.abspath(data_dir), os.path.abspath(app_dir))
        for d in (p.data_dir, p.logs_dir, p.output_dir, p.session_cache_dir):
            os.makedirs(d, exist_ok=True)
        return p

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "app.db")

    @property
    def key_path(self) -> str:
        return os.path.join(self.data_dir, "secret.key")

    @property
    def logs_dir(self) -> str:
        return os.path.join(self.data_dir, "logs")

    @property
    def output_dir(self) -> str:
        return os.path.join(self.data_dir, "output")

    @property
    def session_cache_dir(self) -> str:
        return os.path.join(self.data_dir, "session_cache")

    @property
    def config_yaml(self) -> str:
        return os.path.join(self.app_dir, "config.yaml")

    @property
    def env_file(self) -> str:
        return os.path.join(self.app_dir, ".env")
