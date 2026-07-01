from __future__ import annotations
import json
import os
import time

class SessionStore:
    def __init__(self, cache_dir: str, now_fn=time.time):
        self.cache_dir = cache_dir
        self.now_fn = now_fn
        os.makedirs(cache_dir, exist_ok=True)
        self._last_poll: dict[str, float] = {}

    def _path(self, platform: str) -> str:
        return os.path.join(self.cache_dir, f"{platform}.json")

    def save(self, platform: str, data: dict, ttl_seconds: int) -> None:
        payload = {"expires_at": self.now_fn() + ttl_seconds, "data": data}
        with open(self._path(platform), "w", encoding="utf-8") as f:
            json.dump(payload, f)

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

    def can_poll(self, platform: str, min_interval_s: int) -> bool:
        last = self._last_poll.get(platform)
        return last is None or (self.now_fn() - last) >= min_interval_s

    def mark_poll(self, platform: str) -> None:
        self._last_poll[platform] = self.now_fn()
