from __future__ import annotations
import base64
import hashlib
import hmac
import json
import os
import time

_ITERS = 600_000
CSRF_HEADER = "x-solar-csrf"


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERS)
    return f"pbkdf2_sha256${_ITERS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not isinstance(stored, str):
        return False
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt_hex), int(iters))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sig(secret_key: bytes, payload_b64: str) -> str:
    mac = hmac.new(secret_key, payload_b64.encode("ascii"), hashlib.sha256)
    return _b64e(mac.digest())


def make_cookie(secret_key: bytes, epoch: int) -> str:
    payload = _b64e(json.dumps({"epoch": epoch}).encode("utf-8"))
    return f"{payload}.{_sig(secret_key, payload)}"


def check_cookie(secret_key: bytes, cookie: str, current_epoch: int) -> bool:
    try:
        payload_b64, sig = cookie.split(".", 1)
    except (ValueError, AttributeError):
        return False
    if not hmac.compare_digest(sig, _sig(secret_key, payload_b64)):
        return False
    try:
        data = json.loads(_b64d(payload_b64))
    except (ValueError, TypeError):
        return False
    return isinstance(data, dict) and data.get("epoch") == current_epoch


class RateLimiter:
    def __init__(self, max_fails: int, window_s: float, now_fn=time.time):
        self.max_fails = max_fails
        self.window_s = window_s
        self.now_fn = now_fn
        self._fails: dict[str, list[float]] = {}

    def _recent(self, ip: str) -> list[float]:
        cutoff = self.now_fn() - self.window_s
        keep = [t for t in self._fails.get(ip, []) if t > cutoff]
        if keep:
            self._fails[ip] = keep
        else:
            self._fails.pop(ip, None)
        return keep

    def record_failure(self, ip: str) -> None:
        self._fails.setdefault(ip, []).append(self.now_fn())

    def is_blocked(self, ip: str) -> bool:
        return len(self._recent(ip)) >= self.max_fails

    def reset(self, ip: str) -> None:
        self._fails.pop(ip, None)
