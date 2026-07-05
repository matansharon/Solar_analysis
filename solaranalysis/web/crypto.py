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
    # O_EXCL makes creation atomic: a concurrent first-caller that already
    # created the file loses the race here and we fall back to its key, so we
    # never clobber (O_TRUNC) a key some ciphertext was already encrypted under.
    # On POSIX the 0o600 mode applies at creation. On Windows the mode is
    # ignored and the file briefly inherits the parent dir's ACL until
    # _restrict() tightens it; this narrow window is an accepted limitation per
    # the spec's threat model (specs/2026-07-04-web-ui-design.md §4, which
    # treats filesystem access as already game-over and rejects pywin32/DPAPI).
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        with open(path, "rb") as f:
            return f.read().strip()
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
