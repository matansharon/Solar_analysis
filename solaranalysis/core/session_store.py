from __future__ import annotations
import json
import os
import time

class SessionStore:
    def __init__(self, cache_dir: str, now_fn=time.time):
        self.cache_dir = cache_dir
        self.now_fn = now_fn
        os.makedirs(cache_dir, exist_ok=True)

    def _path(self, platform: str) -> str:
        return os.path.join(self.cache_dir, f"{platform}.json")

    def save(self, platform: str, data: dict, ttl_seconds: int) -> None:
        payload = {"expires_at": self.now_fn() + ttl_seconds, "data": data}
        # Write-then-rename so a crash mid-write can't destroy a valid session.
        tmp = self._path(platform) + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp, self._path(platform))
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def load(self, platform: str) -> dict | None:
        path = self._path(platform)
        if not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
        if payload.get("expires_at", 0) <= self.now_fn():
            return None
        return payload.get("data")
