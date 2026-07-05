from __future__ import annotations
import os
import stat
from cryptography.fernet import Fernet


def load_or_create_key(path: str) -> bytes:
    """Return the Fernet key at ``path``, creating an owner-only file if absent."""
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read().strip()
    key = Fernet.generate_key()
    # Create with restrictive perms where the OS honors them (POSIX); on
    # Windows, tighten the ACL to the current user via icacls best-effort.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    _restrict(path)
    return key


def _restrict(path: str) -> None:
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # no-op-ish on Windows
    except OSError:
        pass
    if os.name == "nt":
        try:
            import subprocess
            user = os.environ.get("USERNAME") or ""
            if user:
                # Remove inheritance, grant only the current user.
                subprocess.run(["icacls", path, "/inheritance:r",
                                "/grant:r", f"{user}:F"],
                               capture_output=True, check=False)
        except Exception:
            pass


def encrypt(key: bytes, plaintext: str) -> bytes:
    return Fernet(key).encrypt(plaintext.encode("utf-8"))


def decrypt(key: bytes, token: bytes) -> str:
    return Fernet(key).decrypt(token).decode("utf-8")
