# Solar Analysis Web UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a LAN-deployed FastAPI + React web UI that owns the solar-analysis config, portal credentials, run history and schedules in SQLite, runs the pipeline as an isolated subprocess with live SSE progress, and serves reports XSS-hardened.

**Architecture:** One uvicorn process serves a JSON API under `/api` and the built React SPA as static files. State lives in SQLite (`<data>/app.db`, WAL). Portal secrets are Fernet-encrypted at rest. Pipeline runs and connection tests execute in short-lived subprocesses that emit JSON-line events on stdout; the server pumps those to a log file and to browsers via Server-Sent Events. All filesystem paths derive from two absolute roots (`--data-dir`, `--app-dir`), never the process cwd.

**Tech Stack:** Python 3.10, FastAPI, uvicorn, sqlite3 (stdlib), cryptography (Fernet), APScheduler, psutil, pytest. Frontend: Vite + React + TypeScript, TanStack Query, React Router.

## Global Constraints

- **Python interpreter:** `python` (not `python3`). Interpreter path on the dev machine: `C:\Users\Matan\AppData\Local\Programs\Python\Python310\python.exe`.
- **Existing tests must stay green.** The CLI (`solaranalysis/cli.py` + `config.yaml`) and all current adapter/pipeline tests keep working byte-identically. Changes to `pipeline.py`, `base.py`, adapters, `session_store.py`, and `cli.py` are additive/backward-compatible.
- **No AI attribution in commits** (repo convention; the clean-commits skill governs). Do NOT add `Co-Authored-By` or similar trailers.
- **Secrets never leave the server in cleartext.** The API never returns password/token values; responses expose only `has_password`/`has_token` booleans. Secrets never appear in logs, SSE, or DB text columns (stream-wide redaction).
- **No cwd dependence.** Every filesystem path resolves against `Paths.data_dir` or `Paths.app_dir` (both absolute).
- **Platforms** are exactly `solaredge`, `growatt`, `sma`. **Auth modes** are `password` (all) and `token` (Growatt only). **Time ranges** are `snapshot`, `30d`, `12mo`, `all`.
- **Run statuses:** `running`, `success` (report written, 0 skipped), `partial` (report written, ≥1 skipped — including all skipped), `failed` (no report file written), `cancelled`, `interrupted`.
- **TDD, DRY, YAGNI, frequent commits.** One behavior per test; commit at the end of each task.
- Run the full backend suite with `python -m pytest -q` from the project root.

---

## File Structure

**New backend package `solaranalysis/web/`:**

| File | Responsibility |
|------|----------------|
| `paths.py` | `Paths` dataclass: resolve `data-dir`/`app-dir` absolutely; expose db/key/logs/output/session_cache/config/env paths; create dirs. |
| `crypto.py` | Fernet key load-or-create (ACL-restricted); `encrypt`/`decrypt`. |
| `auth.py` | PBKDF2 password hash/verify; HMAC session cookie make/check with epoch; CSRF header check; in-memory login rate limiter. |
| `db.py` | SQLite schema DDL, migrations, connection factory (WAL, row factory). |
| `repo.py` | Data-access functions for settings, plants (encrypt/decrypt), schedules, runs. Routes stay thin. |
| `events.py` | `Redactor` (stream-wide secret masking); event line parse/emit helpers. |
| `runner.py` | Subprocess entry (`python -m solaranalysis.web.runner`): `--run` builds `AppConfig` from DB and runs the pipeline emitting events; `--test` calls `verify_login`. |
| `run_manager.py` | Single global operation lock; spawn/track/cancel subprocess; stdout pump → log + SSE; exit-status resolution; startup reconciliation. |
| `scheduler.py` | APScheduler wiring from the `schedules` table. |
| `importer.py` | One-time `config.yaml`/`.env` → DB import. |
| `app.py` | FastAPI app factory: middleware (auth/CSRF), mount routers, static + SPA + report serving. |
| `routes/auth.py` `routes/plants.py` `routes/settings.py` `routes/schedules.py` `routes/runs.py` `routes/imports.py` | HTTP endpoints per resource. |
| `__main__.py` | CLI arg parsing + uvicorn launch. |

**Existing files modified (additive):**

| File | Change |
|------|--------|
| `solaranalysis/core/session_store.py` | Sanitize keys into safe filenames (Windows-safe). |
| `solaranalysis/adapters/base.py` | Per-plant session key derived from auth; abstract `verify_login`. |
| `solaranalysis/adapters/solaredge.py` / `growatt.py` / `sma.py` | Extract `_authenticate(bs, had_state)`; add `verify_login`. |
| `solaranalysis/pipeline.py` | Optional `progress` callback. |
| `solaranalysis/core/report.py` | `append_unavailable_section(md, skipped)` helper (factored from cli.py). |
| `solaranalysis/cli.py` | Use the shared helper. |
| `requirements.txt` | Add `fastapi`, `uvicorn[standard]`, `apscheduler`, `cryptography`, `psutil`. |

**Frontend `frontend/`:** Vite React TS app; `src/api.ts` (fetch + CSRF + 401), `src/sse.ts` (EventSource hook), `src/auth.tsx` (gate/context), `src/routes/*` (five views), built to `frontend/dist` (gitignored, served by FastAPI).

---

## Phase 1 — Scaffolding, paths, crypto, auth primitives

### Task 1: Dependencies and web package skeleton

**Files:**
- Modify: `requirements.txt`
- Create: `solaranalysis/web/__init__.py`
- Create: `solaranalysis/web/routes/__init__.py`
- Modify: `.gitignore`

- [ ] **Step 1: Add backend dependencies**

Append to `requirements.txt`:

```
fastapi>=0.110
uvicorn[standard]>=0.29
apscheduler>=3.10
cryptography>=42.0
psutil>=5.9
httpx>=0.27
```

(`httpx` is needed by FastAPI's `TestClient`.)

- [ ] **Step 2: Install**

Run: `python -m pip install -r requirements.txt`
Expected: all install without error.

- [ ] **Step 3: Create empty package files**

`solaranalysis/web/__init__.py`:

```python
"""Web UI for solar-analysis: FastAPI server + React SPA."""
```

`solaranalysis/web/routes/__init__.py`:

```python
```

- [ ] **Step 4: Ignore runtime data**

Append to `.gitignore`:

```
/data/
frontend/dist/
frontend/node_modules/
```

- [ ] **Step 5: Commit**

```bash
git add requirements.txt solaranalysis/web/__init__.py solaranalysis/web/routes/__init__.py .gitignore
git commit -m "chore(web): add web deps and package skeleton"
```

---

### Task 2: Paths module

**Files:**
- Create: `solaranalysis/web/paths.py`
- Test: `tests/web/test_paths.py`

**Interfaces:**
- Produces: `Paths` dataclass with `data_dir: str`, `app_dir: str` (both absolute) and properties `db_path`, `key_path`, `logs_dir`, `output_dir`, `session_cache_dir`, `config_yaml`, `env_file` (all absolute `str`); classmethod `Paths.create(data_dir: str, app_dir: str) -> Paths` resolves to absolute paths and creates `data_dir`, `logs_dir`, `output_dir`, `session_cache_dir`.

- [ ] **Step 1: Write the failing test**

Create `tests/web/__init__.py` (empty) and `tests/web/test_paths.py`:

```python
import os
from solaranalysis.web.paths import Paths


def test_create_resolves_absolute_and_makes_dirs(tmp_path):
    data = tmp_path / "d"
    app = tmp_path / "a"
    app.mkdir()
    p = Paths.create(str(data), str(app))
    assert os.path.isabs(p.data_dir) and os.path.isabs(p.app_dir)
    assert os.path.isdir(p.logs_dir)
    assert os.path.isdir(p.output_dir)
    assert os.path.isdir(p.session_cache_dir)
    assert p.db_path == os.path.join(p.data_dir, "app.db")
    assert p.key_path == os.path.join(p.data_dir, "secret.key")
    assert p.config_yaml == os.path.join(p.app_dir, "config.yaml")
    assert p.env_file == os.path.join(p.app_dir, ".env")


def test_paths_independent_of_cwd(tmp_path, monkeypatch):
    data = tmp_path / "d"
    app = tmp_path / "a"
    app.mkdir()
    p = Paths.create(str(data), str(app))
    monkeypatch.chdir(tmp_path)
    # Re-reading the property must not depend on cwd.
    assert os.path.isabs(p.output_dir)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/web/test_paths.py -v`
Expected: FAIL (module `solaranalysis.web.paths` not found).

- [ ] **Step 3: Implement**

`solaranalysis/web/paths.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/web/test_paths.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add tests/web/__init__.py tests/web/test_paths.py solaranalysis/web/paths.py
git commit -m "feat(web): cwd-independent Paths module"
```

---

### Task 3: Crypto (Fernet key + encrypt/decrypt)

**Files:**
- Create: `solaranalysis/web/crypto.py`
- Test: `tests/web/test_crypto.py`

**Interfaces:**
- Produces: `load_or_create_key(path: str) -> bytes` (creates a Fernet key file if absent, restricts its permissions, returns the raw key bytes); `encrypt(key: bytes, plaintext: str) -> bytes`; `decrypt(key: bytes, token: bytes) -> str`.

- [ ] **Step 1: Write the failing test**

`tests/web/test_crypto.py`:

```python
import os
import pytest
from solaranalysis.web import crypto


def test_roundtrip(tmp_path):
    key = crypto.load_or_create_key(str(tmp_path / "secret.key"))
    tok = crypto.encrypt(key, "hunter2")
    assert isinstance(tok, bytes)
    assert tok != b"hunter2"
    assert crypto.decrypt(key, tok) == "hunter2"


def test_key_is_stable_and_file_created(tmp_path):
    kp = tmp_path / "secret.key"
    k1 = crypto.load_or_create_key(str(kp))
    assert kp.exists()
    k2 = crypto.load_or_create_key(str(kp))
    assert k1 == k2  # second call reuses the file


def test_wrong_key_cannot_decrypt(tmp_path):
    k1 = crypto.load_or_create_key(str(tmp_path / "a.key"))
    k2 = crypto.load_or_create_key(str(tmp_path / "b.key"))
    tok = crypto.encrypt(k1, "secret")
    with pytest.raises(Exception):
        crypto.decrypt(k2, tok)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/web/test_crypto.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement**

`solaranalysis/web/crypto.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/web/test_crypto.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add tests/web/test_crypto.py solaranalysis/web/crypto.py
git commit -m "feat(web): Fernet credential encryption"
```

---

### Task 4: Auth primitives (password hash, session cookie, CSRF, rate limit)

**Files:**
- Create: `solaranalysis/web/auth.py`
- Test: `tests/web/test_auth_primitives.py`

**Interfaces:**
- Produces:
  - `hash_password(password: str) -> str` → `"pbkdf2_sha256$<iters>$<salt_hex>$<hash_hex>"`.
  - `verify_password(password: str, stored: str) -> bool` (constant-time compare).
  - `make_cookie(secret_key: bytes, epoch: int) -> str` → `"<b64url(json)>.<b64url(hmac)>"`.
  - `check_cookie(secret_key: bytes, cookie: str, current_epoch: int) -> bool` (valid signature AND payload epoch == current_epoch).
  - `RateLimiter(max_fails: int, window_s: float, now_fn=time.time)` with `record_failure(ip: str) -> None`, `is_blocked(ip: str) -> bool`, `reset(ip: str) -> None`.
  - Constant `CSRF_HEADER = "x-solar-csrf"`.

- [ ] **Step 1: Write the failing test**

`tests/web/test_auth_primitives.py`:

```python
from solaranalysis.web import auth


def test_password_hash_roundtrip():
    h = auth.hash_password("s3cret")
    assert h.startswith("pbkdf2_sha256$")
    assert auth.verify_password("s3cret", h) is True
    assert auth.verify_password("wrong", h) is False


def test_cookie_valid_then_epoch_invalidates():
    key = b"0" * 32
    c = auth.make_cookie(key, epoch=1)
    assert auth.check_cookie(key, c, current_epoch=1) is True
    # Password change bumps epoch -> old cookie rejected.
    assert auth.check_cookie(key, c, current_epoch=2) is False


def test_cookie_tamper_rejected():
    key = b"0" * 32
    c = auth.make_cookie(key, epoch=1)
    tampered = c[:-2] + ("aa" if not c.endswith("aa") else "bb")
    assert auth.check_cookie(key, tampered, current_epoch=1) is False


def test_cookie_wrong_key_rejected():
    c = auth.make_cookie(b"0" * 32, epoch=1)
    assert auth.check_cookie(b"1" * 32, c, current_epoch=1) is False


def test_rate_limiter_blocks_after_max():
    t = {"now": 1000.0}
    rl = auth.RateLimiter(max_fails=3, window_s=60, now_fn=lambda: t["now"])
    ip = "10.0.0.5"
    for _ in range(3):
        rl.record_failure(ip)
    assert rl.is_blocked(ip) is True
    t["now"] += 61  # window elapsed
    assert rl.is_blocked(ip) is False


def test_rate_limiter_reset_clears():
    rl = auth.RateLimiter(max_fails=1, window_s=60, now_fn=lambda: 0.0)
    rl.record_failure("ip")
    assert rl.is_blocked("ip") is True
    rl.reset("ip")
    assert rl.is_blocked("ip") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/web/test_auth_primitives.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement**

`solaranalysis/web/auth.py`:

```python
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
    return data.get("epoch") == current_epoch


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/web/test_auth_primitives.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add tests/web/test_auth_primitives.py solaranalysis/web/auth.py
git commit -m "feat(web): password hashing, signed session cookies, rate limiter"
```

---

## Phase 2 — Storage layer (schema + repo)

### Task 5: Database schema and connection

**Files:**
- Create: `solaranalysis/web/db.py`
- Test: `tests/web/test_db.py`

**Interfaces:**
- Produces: `connect(db_path: str) -> sqlite3.Connection` (WAL, foreign keys on, `row_factory = sqlite3.Row`); `init_db(conn) -> None` (idempotent DDL for all tables); `SCHEMA_VERSION: int`.

- [ ] **Step 1: Write the failing test**

`tests/web/test_db.py`:

```python
from solaranalysis.web import db


def _conn(tmp_path):
    c = db.connect(str(tmp_path / "app.db"))
    db.init_db(c)
    return c


def test_init_creates_tables(tmp_path):
    c = _conn(tmp_path)
    names = {r["name"] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"plants", "settings", "schedules", "runs"} <= names


def test_init_is_idempotent(tmp_path):
    c = _conn(tmp_path)
    db.init_db(c)  # second call must not raise
    assert c.execute("SELECT COUNT(*) AS n FROM plants").fetchone()["n"] == 0


def test_wal_and_row_factory(tmp_path):
    c = _conn(tmp_path)
    mode = c.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    c.execute("INSERT INTO settings(key,value) VALUES('k','v')")
    row = c.execute("SELECT value FROM settings WHERE key='k'").fetchone()
    assert row["value"] == "v"  # Row supports name access


def test_platform_check_constraint(tmp_path):
    import sqlite3
    import pytest
    c = _conn(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO plants(name,platform) VALUES('x','bogus')")
        c.commit()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/web/test_db.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement**

`solaranalysis/web/db.py`:

```python
from __future__ import annotations
import sqlite3

SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS plants(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  platform TEXT NOT NULL CHECK (platform IN ('solaredge','growatt','sma')),
  auth_mode TEXT NOT NULL DEFAULT 'password' CHECK (auth_mode IN ('password','token')),
  username TEXT,
  password_enc BLOB,
  token_enc BLOB,
  tariff_per_kwh REAL,
  currency TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  last_test_at TEXT,
  last_test_ok INTEGER,
  last_test_error TEXT
);
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS schedules(
  id INTEGER PRIMARY KEY,
  time_of_day TEXT NOT NULL,
  days_of_week TEXT NOT NULL,
  time_range TEXT NOT NULL CHECK (time_range IN ('snapshot','30d','12mo','all')),
  enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN
    ('running','success','partial','failed','cancelled','interrupted')),
  trigger TEXT NOT NULL CHECK (trigger IN ('manual','scheduled')),
  time_range TEXT NOT NULL CHECK (time_range IN ('snapshot','30d','12mo','all')),
  runner_pid INTEGER,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  report_path TEXT,
  log_path TEXT NOT NULL,
  plants_summary TEXT,
  skipped_plants TEXT,
  notes TEXT,
  error TEXT
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    conn.execute(
        "INSERT INTO settings(key,value) VALUES('schema_version',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),))
    conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/web/test_db.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add tests/web/test_db.py solaranalysis/web/db.py
git commit -m "feat(web): SQLite schema and connection"
```

---

### Task 6: Repo — settings accessors

**Files:**
- Create: `solaranalysis/web/repo.py`
- Test: `tests/web/test_repo_settings.py`

**Interfaces:**
- Produces (in `repo.py`):
  - `get_setting(conn, key, default=None) -> str | None`
  - `set_setting(conn, key, value) -> None`
  - `get_app_settings(conn) -> dict` → `{"model": str|None, "max_input_tokens": int, "output_language": str}` with defaults `None`, `60000`, `"en"`.
  - `set_app_settings(conn, model, max_input_tokens, output_language) -> None`
  - `get_session_epoch(conn) -> int` (default 0); `bump_session_epoch(conn) -> int`
  - `get_password_hash(conn) -> str | None`; `set_password_hash(conn, h) -> None`
  - `setup_required(conn) -> bool` (True when no password_hash)
  - `get_setup_token_hash(conn) -> str | None`; `set_setup_token_hash(conn, h) -> None`; `clear_setup_token(conn) -> None`

- [ ] **Step 1: Write the failing test**

`tests/web/test_repo_settings.py`:

```python
from solaranalysis.web import db, repo


def _conn(tmp_path):
    c = db.connect(str(tmp_path / "app.db"))
    db.init_db(c)
    return c


def test_app_settings_defaults(tmp_path):
    c = _conn(tmp_path)
    s = repo.get_app_settings(c)
    assert s == {"model": None, "max_input_tokens": 60000, "output_language": "en"}


def test_app_settings_roundtrip(tmp_path):
    c = _conn(tmp_path)
    repo.set_app_settings(c, model="claude-opus-4-8",
                          max_input_tokens=1000, output_language="he")
    s = repo.get_app_settings(c)
    assert s["model"] == "claude-opus-4-8"
    assert s["max_input_tokens"] == 1000
    assert s["output_language"] == "he"


def test_epoch_starts_zero_and_bumps(tmp_path):
    c = _conn(tmp_path)
    assert repo.get_session_epoch(c) == 0
    assert repo.bump_session_epoch(c) == 1
    assert repo.get_session_epoch(c) == 1


def test_setup_required_until_password_set(tmp_path):
    c = _conn(tmp_path)
    assert repo.setup_required(c) is True
    repo.set_password_hash(c, "pbkdf2_sha256$1$aa$bb")
    assert repo.setup_required(c) is False
    assert repo.get_password_hash(c) == "pbkdf2_sha256$1$aa$bb"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/web/test_repo_settings.py -v`
Expected: FAIL (module/functions not found).

- [ ] **Step 3: Implement**

Create `solaranalysis/web/repo.py`:

```python
from __future__ import annotations
import sqlite3


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value) -> None:
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, None if value is None else str(value)))
    conn.commit()


def get_app_settings(conn) -> dict:
    return {
        "model": get_setting(conn, "model", None),
        "max_input_tokens": int(get_setting(conn, "max_input_tokens", "60000")),
        "output_language": get_setting(conn, "output_language", "en"),
    }


def set_app_settings(conn, model, max_input_tokens, output_language) -> None:
    set_setting(conn, "model", model)
    set_setting(conn, "max_input_tokens", int(max_input_tokens))
    set_setting(conn, "output_language", output_language)


def get_session_epoch(conn) -> int:
    return int(get_setting(conn, "session_epoch", "0"))


def bump_session_epoch(conn) -> int:
    nxt = get_session_epoch(conn) + 1
    set_setting(conn, "session_epoch", nxt)
    return nxt


def get_password_hash(conn):
    return get_setting(conn, "password_hash", None)


def set_password_hash(conn, h) -> None:
    set_setting(conn, "password_hash", h)


def setup_required(conn) -> bool:
    return get_password_hash(conn) is None


def get_setup_token_hash(conn):
    return get_setting(conn, "setup_token", None)


def set_setup_token_hash(conn, h) -> None:
    set_setting(conn, "setup_token", h)


def clear_setup_token(conn) -> None:
    conn.execute("DELETE FROM settings WHERE key='setup_token'")
    conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/web/test_repo_settings.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add tests/web/test_repo_settings.py solaranalysis/web/repo.py
git commit -m "feat(web): settings repo accessors"
```

---

### Task 7: Repo — plants CRUD with encryption

**Files:**
- Modify: `solaranalysis/web/repo.py`
- Test: `tests/web/test_repo_plants.py`

**Interfaces:**
- Produces (in `repo.py`):
  - `plant_public(row) -> dict`: `{id,name,platform,auth_mode,username,has_password,has_token,tariff_per_kwh,currency,enabled,last_test_at,last_test_ok,last_test_error}` — **never** secret values.
  - `list_plants(conn) -> list[dict]` (public shape); `get_plant(conn, id) -> dict | None` (public).
  - `create_plant(conn, key, data: dict) -> int` where `data` has `name, platform, auth_mode, username, password?, token?, tariff_per_kwh?, currency?, enabled?`. Encrypts `password`/`token` with Fernet `key`.
  - `update_plant(conn, key, id, data: dict) -> None`: fields present are updated; `password`/`token` omitted or empty ("") → keep existing; switching platform away from growatt forces `auth_mode='password'` and clears `token_enc`.
  - `delete_plant(conn, id) -> None`
  - `set_plant_test_result(conn, id, ok: bool, error: str | None, at: str) -> None`
  - `load_plant_auth(conn, key, id) -> AuthConfig | None`: decrypts secrets into the existing `solaranalysis.config.AuthConfig`.
- Consumes: `crypto.encrypt/decrypt` (Task 3); `solaranalysis.config.AuthConfig`.

- [ ] **Step 1: Write the failing test**

`tests/web/test_repo_plants.py`:

```python
from solaranalysis.web import db, repo, crypto


def _ctx(tmp_path):
    c = db.connect(str(tmp_path / "app.db"))
    db.init_db(c)
    key = crypto.load_or_create_key(str(tmp_path / "secret.key"))
    return c, key


def test_create_and_public_hides_secrets(tmp_path):
    c, key = _ctx(tmp_path)
    pid = repo.create_plant(c, key, {
        "name": "Roof", "platform": "solaredge", "auth_mode": "password",
        "username": "a@b.com", "password": "pw", "tariff_per_kwh": 0.5,
        "currency": "ILS"})
    p = repo.get_plant(c, pid)
    assert p["name"] == "Roof" and p["has_password"] is True
    assert p["has_token"] is False
    assert "password" not in p and "password_enc" not in p


def test_load_plant_auth_decrypts(tmp_path):
    c, key = _ctx(tmp_path)
    pid = repo.create_plant(c, key, {
        "name": "G", "platform": "growatt", "auth_mode": "password",
        "username": "u", "password": "pw"})
    auth = repo.load_plant_auth(c, key, pid)
    assert auth.platform == "growatt"
    assert auth.username == "u" and auth.password == "pw"


def test_update_blank_password_keeps_existing(tmp_path):
    c, key = _ctx(tmp_path)
    pid = repo.create_plant(c, key, {
        "name": "G", "platform": "growatt", "auth_mode": "password",
        "username": "u", "password": "orig"})
    repo.update_plant(c, key, pid, {"username": "u2", "password": ""})
    auth = repo.load_plant_auth(c, key, pid)
    assert auth.username == "u2" and auth.password == "orig"


def test_switch_platform_off_growatt_clears_token(tmp_path):
    c, key = _ctx(tmp_path)
    pid = repo.create_plant(c, key, {
        "name": "G", "platform": "growatt", "auth_mode": "token",
        "token": "tok"})
    repo.update_plant(c, key, pid, {"platform": "sma"})
    p = repo.get_plant(c, pid)
    assert p["platform"] == "sma" and p["auth_mode"] == "password"
    assert p["has_token"] is False


def test_test_result_recorded(tmp_path):
    c, key = _ctx(tmp_path)
    pid = repo.create_plant(c, key, {
        "name": "G", "platform": "sma", "auth_mode": "password",
        "username": "u", "password": "p"})
    repo.set_plant_test_result(c, pid, ok=False, error="bad creds",
                               at="2026-07-04T00:00:00")
    p = repo.get_plant(c, pid)
    assert p["last_test_ok"] is False and p["last_test_error"] == "bad creds"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/web/test_repo_plants.py -v`
Expected: FAIL (functions not found).

- [ ] **Step 3: Implement**

Append to `solaranalysis/web/repo.py`:

```python
from . import crypto as _crypto
from ..config import AuthConfig


def plant_public(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "platform": row["platform"],
        "auth_mode": row["auth_mode"],
        "username": row["username"],
        "has_password": row["password_enc"] is not None,
        "has_token": row["token_enc"] is not None,
        "tariff_per_kwh": row["tariff_per_kwh"],
        "currency": row["currency"],
        "enabled": bool(row["enabled"]),
        "last_test_at": row["last_test_at"],
        "last_test_ok": None if row["last_test_ok"] is None else bool(row["last_test_ok"]),
        "last_test_error": row["last_test_error"],
    }


def list_plants(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM plants ORDER BY id").fetchall()
    return [plant_public(r) for r in rows]


def _row(conn, id):
    return conn.execute("SELECT * FROM plants WHERE id=?", (id,)).fetchone()


def get_plant(conn, id):
    r = _row(conn, id)
    return plant_public(r) if r else None


def create_plant(conn, key, data: dict) -> int:
    pw = data.get("password")
    tok = data.get("token")
    cur = conn.execute(
        "INSERT INTO plants(name,platform,auth_mode,username,password_enc,"
        "token_enc,tariff_per_kwh,currency,enabled) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (data["name"], data["platform"], data.get("auth_mode", "password"),
         data.get("username"),
         _crypto.encrypt(key, pw) if pw else None,
         _crypto.encrypt(key, tok) if tok else None,
         data.get("tariff_per_kwh"), data.get("currency"),
         1 if data.get("enabled", True) else 0))
    conn.commit()
    return cur.lastrowid


def update_plant(conn, key, id, data: dict) -> None:
    row = _row(conn, id)
    if row is None:
        raise KeyError(id)
    platform = data.get("platform", row["platform"])
    auth_mode = data.get("auth_mode", row["auth_mode"])
    token_enc = row["token_enc"]
    # Switching away from growatt forces password mode and drops the token.
    if platform != "growatt":
        auth_mode = "password"
        token_enc = None
    password_enc = row["password_enc"]
    if data.get("password"):
        password_enc = _crypto.encrypt(key, data["password"])
    if "token" in data and platform == "growatt":
        token_enc = _crypto.encrypt(key, data["token"]) if data["token"] else token_enc
    conn.execute(
        "UPDATE plants SET name=?,platform=?,auth_mode=?,username=?,"
        "password_enc=?,token_enc=?,tariff_per_kwh=?,currency=?,enabled=? "
        "WHERE id=?",
        (data.get("name", row["name"]), platform, auth_mode,
         data.get("username", row["username"]), password_enc, token_enc,
         data.get("tariff_per_kwh", row["tariff_per_kwh"]),
         data.get("currency", row["currency"]),
         1 if data.get("enabled", bool(row["enabled"])) else 0, id))
    conn.commit()


def delete_plant(conn, id) -> None:
    conn.execute("DELETE FROM plants WHERE id=?", (id,))
    conn.commit()


def set_plant_test_result(conn, id, ok: bool, error, at: str) -> None:
    conn.execute(
        "UPDATE plants SET last_test_at=?,last_test_ok=?,last_test_error=? WHERE id=?",
        (at, 1 if ok else 0, error, id))
    conn.commit()


def load_plant_auth(conn, key, id):
    r = _row(conn, id)
    if r is None:
        return None
    return AuthConfig(
        platform=r["platform"],
        mode=r["auth_mode"],
        username=r["username"],
        password=_crypto.decrypt(key, r["password_enc"]) if r["password_enc"] else None,
        token=_crypto.decrypt(key, r["token_enc"]) if r["token_enc"] else None,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/web/test_repo_plants.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add tests/web/test_repo_plants.py solaranalysis/web/repo.py
git commit -m "feat(web): plants repo with encrypted credentials"
```

---

### Task 8: Repo — schedules and runs

**Files:**
- Modify: `solaranalysis/web/repo.py`
- Test: `tests/web/test_repo_runs.py`

**Interfaces:**
- Produces (in `repo.py`):
  - Schedules: `list_schedules(conn) -> list[dict]`; `create_schedule(conn, data) -> int` (`time_of_day, days_of_week, time_range, enabled`); `update_schedule(conn, id, data) -> None`; `delete_schedule(conn, id) -> None`.
  - Runs: `create_run(conn, trigger, time_range, log_path, started_at) -> int` (status `running`); `set_run_pid(conn, id, pid) -> None`; `get_run(conn, id) -> dict | None`; `list_runs(conn, limit=50, offset=0) -> list[dict]`; `finalize_run(conn, id, *, status, finished_at, report_path, plants_summary, skipped_plants, notes, error) -> None`; `running_runs(conn) -> list[dict]`; `mark_interrupted(conn, id, finished_at) -> None`.
  - `run_public(row) -> dict` (all columns; JSON-decodes `plants_summary`, `skipped_plants`, `notes`).

- [ ] **Step 1: Write the failing test**

`tests/web/test_repo_runs.py`:

```python
from solaranalysis.web import db, repo


def _conn(tmp_path):
    c = db.connect(str(tmp_path / "app.db"))
    db.init_db(c)
    return c


def test_schedule_crud(tmp_path):
    c = _conn(tmp_path)
    sid = repo.create_schedule(c, {"time_of_day": "06:00",
                                   "days_of_week": "0,1,2,3,4",
                                   "time_range": "30d", "enabled": True})
    assert len(repo.list_schedules(c)) == 1
    repo.update_schedule(c, sid, {"enabled": False})
    assert repo.list_schedules(c)[0]["enabled"] is False
    repo.delete_schedule(c, sid)
    assert repo.list_schedules(c) == []


def test_run_lifecycle(tmp_path):
    c = _conn(tmp_path)
    rid = repo.create_run(c, trigger="manual", time_range="30d",
                          log_path="logs/run-1.log", started_at="2026-07-04T00:00:00")
    repo.set_run_pid(c, rid, 4321)
    assert repo.get_run(c, rid)["status"] == "running"
    assert repo.get_run(c, rid)["runner_pid"] == 4321
    assert [r["id"] for r in repo.running_runs(c)] == [rid]
    repo.finalize_run(c, rid, status="partial", finished_at="2026-07-04T00:01:00",
                      report_path="output/x/report.html",
                      plants_summary=[{"name": "A", "ok": True}],
                      skipped_plants=[{"name": "B", "reason": "boom"}],
                      notes={"verify_missing_count": 1}, error=None)
    r = repo.get_run(c, rid)
    assert r["status"] == "partial"
    assert r["plants_summary"] == [{"name": "A", "ok": True}]
    assert r["skipped_plants"][0]["reason"] == "boom"
    assert r["notes"]["verify_missing_count"] == 1
    assert repo.running_runs(c) == []


def test_mark_interrupted(tmp_path):
    c = _conn(tmp_path)
    rid = repo.create_run(c, trigger="scheduled", time_range="all",
                          log_path="logs/run-2.log", started_at="2026-07-04T00:00:00")
    repo.mark_interrupted(c, rid, finished_at="2026-07-04T00:05:00")
    assert repo.get_run(c, rid)["status"] == "interrupted"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/web/test_repo_runs.py -v`
Expected: FAIL (functions not found).

- [ ] **Step 3: Implement**

Append to `solaranalysis/web/repo.py`:

```python
import json as _json


def list_schedules(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM schedules ORDER BY id").fetchall()
    return [{"id": r["id"], "time_of_day": r["time_of_day"],
             "days_of_week": r["days_of_week"], "time_range": r["time_range"],
             "enabled": bool(r["enabled"])} for r in rows]


def create_schedule(conn, data) -> int:
    cur = conn.execute(
        "INSERT INTO schedules(time_of_day,days_of_week,time_range,enabled) "
        "VALUES(?,?,?,?)",
        (data["time_of_day"], data["days_of_week"], data["time_range"],
         1 if data.get("enabled", True) else 0))
    conn.commit()
    return cur.lastrowid


def update_schedule(conn, id, data) -> None:
    row = conn.execute("SELECT * FROM schedules WHERE id=?", (id,)).fetchone()
    if row is None:
        raise KeyError(id)
    conn.execute(
        "UPDATE schedules SET time_of_day=?,days_of_week=?,time_range=?,enabled=? "
        "WHERE id=?",
        (data.get("time_of_day", row["time_of_day"]),
         data.get("days_of_week", row["days_of_week"]),
         data.get("time_range", row["time_range"]),
         1 if data.get("enabled", bool(row["enabled"])) else 0, id))
    conn.commit()


def delete_schedule(conn, id) -> None:
    conn.execute("DELETE FROM schedules WHERE id=?", (id,))
    conn.commit()


def run_public(row) -> dict:
    def _dec(v):
        return _json.loads(v) if v else None
    return {
        "id": row["id"], "status": row["status"], "trigger": row["trigger"],
        "time_range": row["time_range"], "runner_pid": row["runner_pid"],
        "started_at": row["started_at"], "finished_at": row["finished_at"],
        "report_path": row["report_path"], "log_path": row["log_path"],
        "plants_summary": _dec(row["plants_summary"]),
        "skipped_plants": _dec(row["skipped_plants"]),
        "notes": _dec(row["notes"]), "error": row["error"],
    }


def create_run(conn, trigger, time_range, log_path, started_at) -> int:
    cur = conn.execute(
        "INSERT INTO runs(status,trigger,time_range,started_at,log_path) "
        "VALUES('running',?,?,?,?)", (trigger, time_range, started_at, log_path))
    conn.commit()
    return cur.lastrowid


def set_run_pid(conn, id, pid) -> None:
    conn.execute("UPDATE runs SET runner_pid=? WHERE id=?", (pid, id))
    conn.commit()


def get_run(conn, id):
    r = conn.execute("SELECT * FROM runs WHERE id=?", (id,)).fetchone()
    return run_public(r) if r else None


def list_runs(conn, limit=50, offset=0) -> list[dict]:
    rows = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ? OFFSET ?",
                        (limit, offset)).fetchall()
    return [run_public(r) for r in rows]


def running_runs(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM runs WHERE status='running'").fetchall()
    return [run_public(r) for r in rows]


def finalize_run(conn, id, *, status, finished_at, report_path,
                 plants_summary, skipped_plants, notes, error) -> None:
    conn.execute(
        "UPDATE runs SET status=?,finished_at=?,report_path=?,plants_summary=?,"
        "skipped_plants=?,notes=?,error=? WHERE id=?",
        (status, finished_at, report_path,
         _json.dumps(plants_summary) if plants_summary is not None else None,
         _json.dumps(skipped_plants) if skipped_plants is not None else None,
         _json.dumps(notes) if notes is not None else None, error, id))
    conn.commit()


def mark_interrupted(conn, id, finished_at) -> None:
    conn.execute("UPDATE runs SET status='interrupted',finished_at=? WHERE id=?",
                 (finished_at, id))
    conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/web/test_repo_runs.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add tests/web/test_repo_runs.py solaranalysis/web/repo.py
git commit -m "feat(web): schedules and runs repo"
```

---

## Phase 3 — Adapter & pipeline changes (backward-compatible)

### Task 9: Filesystem-safe session keys + per-plant keying

**Files:**
- Modify: `solaranalysis/core/session_store.py`
- Modify: `solaranalysis/adapters/base.py`
- Test: `tests/test_session_store.py` (add cases), `tests/test_adapter_base.py` (add case)

**Interfaces:**
- Produces: `SessionStore._path` sanitizes the key (any char outside `[A-Za-z0-9._-]` → `_`) so keys like `growatt:ab12cd` become valid Windows filenames. `SolarPortalAdapter._session_key() -> str` returns `f"{platform}:{sha1(username or token or '')[:12]}"`; `_load_session`/`_save_session` use it instead of `self.platform`.
- Constraint: existing simple keys (`"growatt"`, `"solaredge"`) are unchanged by sanitization, so `tests/test_session_store.py` stays green.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_session_store.py`:

```python
def test_key_with_colon_is_filesystem_safe(tmp_path):
    from solaranalysis.core.session_store import SessionStore
    s = SessionStore(str(tmp_path))
    s.save("growatt:ab12cd34", {"cookie": "x"}, ttl_seconds=100)
    assert s.load("growatt:ab12cd34") == {"cookie": "x"}
    # Distinct keys do not collide.
    s.save("growatt:zz99", {"cookie": "y"}, ttl_seconds=100)
    assert s.load("growatt:ab12cd34") == {"cookie": "x"}
    assert s.load("growatt:zz99") == {"cookie": "y"}
```

Add to `tests/test_adapter_base.py`:

```python
def test_session_key_is_per_account():
    from solaranalysis.config import AuthConfig
    from solaranalysis.core.session_store import SessionStore
    from solaranalysis.adapters.solaredge import SolarEdgeAdapter
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        ss = SessionStore(d)
        a = SolarEdgeAdapter(AuthConfig("solaredge", username="a@x.com", password="p"), ss)
        b = SolarEdgeAdapter(AuthConfig("solaredge", username="b@x.com", password="p"), ss)
        assert a._session_key() != b._session_key()
        assert a._session_key().startswith("solaredge:")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_session_store.py::test_key_with_colon_is_filesystem_safe tests/test_adapter_base.py::test_session_key_is_per_account -v`
Expected: FAIL (colon key collides/errors; `_session_key` missing).

- [ ] **Step 3: Implement**

In `solaranalysis/core/session_store.py`, replace `_path`:

```python
    def _path(self, platform: str) -> str:
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in platform)
        return os.path.join(self.cache_dir, f"{safe}.json")
```

In `solaranalysis/adapters/base.py`, add to `SolarPortalAdapter` and switch the session helpers to use it:

```python
    def _session_key(self) -> str:
        import hashlib
        ident = self.auth.username or self.auth.token or ""
        h = hashlib.sha1(ident.encode("utf-8")).hexdigest()[:12]
        return f"{self.platform}:{h}"

    def _load_session(self) -> dict | None:
        return self.sessions.load(self._session_key())

    def _save_session(self, bs) -> None:
        try:
            self.sessions.save(self._session_key(), bs.storage_state(), SESSION_TTL_S)
        except Exception:
            pass
```

(Delete the old `_load_session`/`_save_session` bodies that used `self.platform`.)

- [ ] **Step 4: Run the full adapter/session suite**

Run: `python -m pytest tests/test_session_store.py tests/test_adapter_base.py tests/test_solaredge_adapter.py tests/test_growatt_web.py tests/test_sma_adapter.py -v`
Expected: PASS (all, including the two new tests).

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/core/session_store.py solaranalysis/adapters/base.py tests/test_session_store.py tests/test_adapter_base.py
git commit -m "feat(adapters): per-account, filesystem-safe session cache keys"
```

---

### Task 10: Pipeline progress callback

**Files:**
- Modify: `solaranalysis/pipeline.py`
- Test: `tests/test_pipeline.py` (add case)

**Interfaces:**
- Produces: `run_pipeline(cfg, time_range, session_store, adapter_factory=get_adapter, analyzer=run_analysis, progress=None)`. When `progress` is a callable it is invoked with dict events during the loop:
  - `{"event":"plant_start","plant":name}` before each plant
  - `{"event":"plant_step","plant":name,"step":"login"}` before `adapter.login()`
  - `{"event":"plant_step","plant":name,"step":"fetch"}` before `adapter.fetch(...)`
  - `{"event":"plant_done","plant":name,"ok":True}` after success; `{"event":"plant_done","plant":name,"ok":False,"reason":str}` on failure
  - `{"event":"analyze_start"}` before the analyzer runs
- Constraint: `progress=None` (default) must leave behavior byte-identical; existing pipeline tests pass untouched.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pipeline.py`:

```python
def test_pipeline_emits_progress_events(tmp_path):
    cfg = AppConfig(plants=[
        PlantConfig("Bad", AuthConfig("growatt", username="bad", password="p")),
        PlantConfig("Good", AuthConfig("growatt", username="good", password="p")),
    ])
    ss = SessionStore(str(tmp_path))

    class Boom:
        def login(self): raise RuntimeError("auth failed")
        def fetch(self, tr): raise RuntimeError("nope")
    seq = [Boom(), FakeAdapter(_pd("Good"))]
    def factory(auth, store): return seq.pop(0)
    def analyzer(plants, tr, c, client=None): return "ok"

    events = []
    run_pipeline(cfg, TimeRange.SNAPSHOT, ss, adapter_factory=factory,
                 analyzer=analyzer, progress=events.append)
    kinds = [(e["event"], e.get("plant"), e.get("ok", e.get("step")))
             for e in events]
    assert ("plant_start", "Bad", None) in kinds
    assert ("plant_done", "Bad", False) in kinds
    assert ("plant_done", "Good", True) in kinds
    assert ("analyze_start", None, None) in kinds
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pipeline.py::test_pipeline_emits_progress_events -v`
Expected: FAIL (`run_pipeline` got unexpected keyword `progress`).

- [ ] **Step 3: Implement**

Replace the body of `run_pipeline` in `solaranalysis/pipeline.py` (keep `_normalize` unchanged):

```python
def run_pipeline(cfg: AppConfig, time_range: TimeRange, session_store,
                 adapter_factory=get_adapter, analyzer=run_analysis,
                 progress=None) -> dict:
    def emit(**ev):
        if progress:
            progress(ev)
    plants: list[PlantData] = []
    skipped: list[dict] = []
    for pc in cfg.plants:
        emit(event="plant_start", plant=pc.name)
        try:
            adapter = adapter_factory(pc.auth, session_store)
            emit(event="plant_step", plant=pc.name, step="login")
            adapter.login()
            emit(event="plant_step", plant=pc.name, step="fetch")
            fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for pd in adapter.fetch(time_range):
                if pc.currency and not pd.currency:
                    pd.currency = pc.currency
                pd.fetched_at_utc = fetched_at
                plants.append(_normalize(pd, pc))
            emit(event="plant_done", plant=pc.name, ok=True)
        except Exception as e:  # isolate per-plant failures
            print(f"[warn] plant {pc.name!r} unavailable: {e}")
            skipped.append({"name": pc.name, "reason": str(e)})
            emit(event="plant_done", plant=pc.name, ok=False, reason=str(e))
    emit(event="analyze_start")
    report_md = analyzer(plants, time_range, cfg) if plants else "No plant data available."
    data_block = build_data_block(plants, time_range, default_meta(plants))
    return {"report_md": report_md, "plants": plants,
            "verify_missing": verify_numbers(report_md, data_block),
            "skipped_plants": skipped}
```

- [ ] **Step 4: Run the pipeline suite**

Run: `python -m pytest tests/test_pipeline.py -v`
Expected: PASS (all existing + new).

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/pipeline.py tests/test_pipeline.py
git commit -m "feat(pipeline): optional progress callback"
```

---

### Task 11: Real `verify_login` for all adapters

**Files:**
- Modify: `solaranalysis/adapters/base.py`, `solaranalysis/adapters/solaredge.py`, `solaranalysis/adapters/growatt.py`, `solaranalysis/adapters/sma.py`
- Test: `tests/test_verify_login.py`

**Interfaces:**
- Produces: `SolarPortalAdapter.verify_login(self) -> None` (abstract) — performs a real portal authentication and raises `AdapterError` on failure, without fetching plant data. Each concrete adapter extracts its login flow into `_authenticate(self, bs, had_state: bool) -> None` (used by both `fetch` and `verify_login`), so `fetch` behavior is unchanged.

- [ ] **Step 1: Write the failing test**

`tests/test_verify_login.py`:

```python
import pytest
from solaranalysis.config import AuthConfig
from solaranalysis.core.session_store import SessionStore
from solaranalysis.adapters.base import AdapterError
from solaranalysis.adapters.solaredge import SolarEdgeAdapter


class FakeBS:
    """Stands in for BrowserSession; records that _authenticate ran."""
    def __init__(self, fail=False):
        self.fail = fail
        self.authed = False
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def storage_state(self): return {"cookie": "x"}


def test_verify_login_rejects_bad_config(tmp_path):
    ss = SessionStore(str(tmp_path))
    ad = SolarEdgeAdapter(AuthConfig("solaredge", username=None, password=None), ss)
    with pytest.raises(AdapterError):
        ad.verify_login()


def test_verify_login_success(monkeypatch, tmp_path):
    ss = SessionStore(str(tmp_path))
    ad = SolarEdgeAdapter(AuthConfig("solaredge", username="a@x.com", password="p"), ss)
    fake = FakeBS()
    monkeypatch.setattr("solaranalysis.adapters.solaredge.BrowserSession",
                        lambda **k: fake, raising=False)
    monkeypatch.setattr(ad, "_authenticate", lambda bs, had_state: None)
    ad.verify_login()  # must not raise


def test_verify_login_propagates_auth_failure(monkeypatch, tmp_path):
    ss = SessionStore(str(tmp_path))
    ad = SolarEdgeAdapter(AuthConfig("solaredge", username="a@x.com", password="p"), ss)
    monkeypatch.setattr("solaranalysis.adapters.solaredge.BrowserSession",
                        lambda **k: FakeBS(), raising=False)
    def boom(bs, had_state): raise RuntimeError("login timeout")
    monkeypatch.setattr(ad, "_authenticate", boom)
    with pytest.raises(AdapterError):
        ad.verify_login()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_verify_login.py -v`
Expected: FAIL (`verify_login`/`_authenticate` not defined; `BrowserSession` not importable at module scope).

- [ ] **Step 3a: Add abstract method to base**

In `solaranalysis/adapters/base.py`, add to `SolarPortalAdapter`:

```python
    @abstractmethod
    def verify_login(self) -> None:
        """Perform a real portal login; raise AdapterError on failure."""
```

- [ ] **Step 3b: SolarEdge — extract `_authenticate`, add `verify_login`**

In `solaranalysis/adapters/solaredge.py`, add a module-level import near the top (after the constants):

```python
from ._browser import BrowserSession
```

Refactor `SolarEdgeAdapter.fetch` to delegate authentication, and add the two methods:

```python
    def _authenticate(self, bs, had_state: bool) -> None:
        bs.page.goto(f"{_BASE}/", wait_until="domcontentloaded")
        logged_in = False
        if had_state:
            try:
                bs.page.wait_for_url("**/one#/site-list", timeout=10000)
                logged_in = True
            except Exception:
                logged_in = False
        if not logged_in:
            bs.page.get_by_role("button", name="Log in").click()
            bs.page.get_by_role("textbox", name="Email address").fill(self.auth.username)
            bs.page.get_by_role("textbox", name="Password").fill(self.auth.password)
            bs.page.get_by_role("button", name="Sign in").first.click()
            bs.page.wait_for_url("**/one#/site-list", timeout=45000)

    def verify_login(self) -> None:
        self.login()
        state = self._load_session()
        try:
            with BrowserSession(storage_state=state) as bs:
                self._authenticate(bs, had_state=bool(state))
                self._save_session(bs)
        except AdapterError:
            raise
        except Exception as e:
            raise AdapterError(f"solaredge: login failed ({e})")
```

Then in `fetch`, replace the inline goto/login block with:

```python
            store = bs.capture([_SEARCH, _MEAS])
            self._authenticate(bs, had_state=bool(state))
```

(Remove the now-duplicated `bs.page.goto(...)`, the `logged_in` block, and the form-fill lines from `fetch`; keep the response-polling and mapping that follow. Delete the local `from ._browser import BrowserSession` inside `fetch` since it is now module-level.)

- [ ] **Step 3c: Growatt — extract `_authenticate`, add `verify_login`**

In `solaranalysis/adapters/growatt.py`, add module-level `from ._browser import BrowserSession` after `_HOST`. Add to `GrowattAdapter`:

```python
    def _authenticate(self, bs, had_state: bool) -> None:
        logged_in = False
        if had_state:
            bs.page.goto(f"{_HOST}/index", wait_until="domcontentloaded")
            try:
                bs.page.wait_for_url("**/index**", timeout=10000)
                logged_in = True
            except Exception:
                logged_in = False
        if not logged_in:
            bs.page.goto(f"{_HOST}/login", wait_until="domcontentloaded")
            try:
                bs.page.get_by_role("button", name="Agree").click(timeout=4000)
            except Exception:
                pass
            bs.page.get_by_role("textbox", name="User Name").fill(self.auth.username)
            bs.page.get_by_role("textbox", name="Password").fill(self.auth.password)
            bs.page.get_by_role("button", name="Login").click()
            bs.page.wait_for_url("**/index**", timeout=45000)

    def verify_login(self) -> None:
        self.login()
        if self.auth.mode == "token":
            try:
                self._client.plant_list()
            except Exception as e:
                raise AdapterError(f"growatt: token login failed ({e})")
            return
        state = self._load_session()
        try:
            with BrowserSession(storage_state=state) as bs:
                self._authenticate(bs, had_state=bool(state))
                self._save_session(bs)
        except AdapterError:
            raise
        except Exception as e:
            raise AdapterError(f"growatt: login failed ({e})")
```

In `_fetch_web`, replace the inline login block with `self._authenticate(bs, had_state=bool(state))` immediately after `store = bs.capture([...])`, then keep `bs.page.wait_for_timeout(4000)` and the rest. Remove the local `from ._browser import BrowserSession` (now module-level).

- [ ] **Step 3d: SMA — extract `_authenticate`, add `verify_login`**

In `solaranalysis/adapters/sma.py`, add module-level `from ._browser import BrowserSession` after the constants. Add to `SMAAdapter`:

```python
    def _authenticate(self, bs, had_state: bool) -> None:
        bs.page.goto(_PLANTS_URL, wait_until="domcontentloaded")
        if bs.page.locator(_LOGIN_BUTTON).count():
            bs.page.locator(_LOGIN_BUTTON).click()
            bs.page.wait_for_url("**login.sma.energy**", timeout=30000)
            bs.page.get_by_role("textbox", name="E-mail or user name").fill(self.auth.username)
            bs.page.get_by_role("textbox", name="Password").fill(self.auth.password)
            bs.page.get_by_role("button", name="Log in").click()
            bs.page.wait_for_url("**sunnyportal.com/Plants**", timeout=45000)

    def verify_login(self) -> None:
        self.login()
        state = self._load_session()
        try:
            with BrowserSession(storage_state=state) as bs:
                self._authenticate(bs, had_state=bool(state))
                self._save_session(bs)
        except AdapterError:
            raise
        except Exception as e:
            raise AdapterError(f"sma: login failed ({e})")
```

In `fetch`, replace the inline goto/login block with `self._authenticate(bs, had_state=bool(state))`, keep the row-polling that follows, and remove the local `from ._browser import BrowserSession`.

- [ ] **Step 4: Run the adapter suite**

Run: `python -m pytest tests/test_verify_login.py tests/test_solaredge_adapter.py tests/test_growatt_web.py tests/test_sma_adapter.py tests/test_adapter_fetch.py -v`
Expected: PASS (new verify tests + all existing mapper/fetch tests unchanged).

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/adapters/base.py solaranalysis/adapters/solaredge.py solaranalysis/adapters/growatt.py solaranalysis/adapters/sma.py tests/test_verify_login.py
git commit -m "feat(adapters): real verify_login via extracted _authenticate"
```

---

### Task 12: Shared "Unavailable Plants" report helper

**Files:**
- Modify: `solaranalysis/core/report.py`
- Modify: `solaranalysis/cli.py`
- Test: `tests/test_report.py` (add case)

**Interfaces:**
- Produces: `append_unavailable_section(report_md: str, skipped: list[dict]) -> str` in `report.py` — returns `report_md` unchanged when `skipped` is empty; otherwise appends a `## Unavailable Plants` section with each `name`/`reason` HTML-escaped (identical output to the current cli.py inline logic).
- Consumes: used by `cli.py` (now) and `runner.py` (Task 14).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_report.py`:

```python
def test_append_unavailable_section_escapes_and_skips_when_empty():
    from solaranalysis.core.report import append_unavailable_section
    assert append_unavailable_section("# R", []) == "# R"
    out = append_unavailable_section("# R", [
        {"name": "Bad <b>P</b>", "reason": "boom <script>x</script>"}])
    assert "## Unavailable Plants" in out
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_report.py::test_append_unavailable_section_escapes_and_skips_when_empty -v`
Expected: FAIL (function not found).

- [ ] **Step 3: Implement helper and adopt in CLI**

Add to `solaranalysis/core/report.py`:

```python
from html import escape as _escape


def append_unavailable_section(report_md: str, skipped: list[dict]) -> str:
    if not skipped:
        return report_md
    lines = "\n".join(f"- **{_escape(s['name'])}**: {_escape(s['reason'])}"
                      for s in skipped)
    return (report_md + "\n\n## Unavailable Plants\n\nThe following plants "
            "could not be fetched for this run:\n\n" + lines)
```

In `solaranalysis/cli.py`, replace the inline skipped-plants markdown block (the `lines = "\n".join(...)` and the `report_md += ...` inside `if res["skipped_plants"]:`) with:

```python
        report_md = append_unavailable_section(report_md, res["skipped_plants"])
```

and add `append_unavailable_section` to the `from .core.report import ...` line. Keep the existing stderr `[warn]` print.

- [ ] **Step 4: Run the report + CLI suite**

Run: `python -m pytest tests/test_report.py tests/test_cli.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add solaranalysis/core/report.py solaranalysis/cli.py tests/test_report.py
git commit -m "refactor(report): shared Unavailable Plants helper"
```

---

## Phase 4 — Events, redaction, and the runner subprocess

### Task 13: Redaction + event framing

**Files:**
- Create: `solaranalysis/web/events.py`
- Test: `tests/web/test_events.py`

**Interfaces:**
- Produces:
  - `Redactor(secrets: list[str])` with `redact(text: str) -> str` — replaces every non-empty secret substring with `***`; ignores empty/None secrets; longest-first so overlapping secrets fully mask.
  - `EVENT_PREFIX = "@@EVENT@@ "` — a sentinel prepended to JSON event lines so the server distinguishes structured events from plain log lines.
  - `emit_event(ev: dict) -> None` — prints `EVENT_PREFIX + json.dumps(ev)` to stdout, flushed.
  - `parse_line(line: str) -> tuple[str, object]` — returns `("event", dict)` if the line starts with `EVENT_PREFIX` and parses, else `("log", line)`.

- [ ] **Step 1: Write the failing test**

`tests/web/test_events.py`:

```python
from solaranalysis.web import events


def test_redactor_masks_all_secrets():
    r = events.Redactor(["hunter2", "tok-abc"])
    out = r.redact("user pw=hunter2 token=tok-abc done")
    assert "hunter2" not in out and "tok-abc" not in out
    assert out.count("***") == 2


def test_redactor_ignores_empty_secrets():
    r = events.Redactor(["", None, "pw"])
    assert r.redact("x pw y") == "x *** y"
    assert r.redact("nothing here") == "nothing here"


def test_redactor_overlapping_longest_first():
    r = events.Redactor(["abc", "abcdef"])
    # The longer secret must be fully masked, not leave "def".
    assert r.redact("val=abcdef") == "val=***"


def test_event_roundtrip():
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        events.emit_event({"event": "plant_start", "plant": "A"})
    line = buf.getvalue().strip()
    kind, val = events.parse_line(line)
    assert kind == "event" and val["plant"] == "A"


def test_parse_plain_line():
    kind, val = events.parse_line("[warn] something happened")
    assert kind == "log" and val == "[warn] something happened"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/web/test_events.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement**

`solaranalysis/web/events.py`:

```python
from __future__ import annotations
import json
import sys

EVENT_PREFIX = "@@EVENT@@ "


class Redactor:
    def __init__(self, secrets):
        # Longest first so a secret that contains another is masked whole.
        self._secrets = sorted({s for s in secrets if s}, key=len, reverse=True)

    def redact(self, text: str) -> str:
        if not text:
            return text
        for s in self._secrets:
            text = text.replace(s, "***")
        return text


def emit_event(ev: dict) -> None:
    sys.stdout.write(EVENT_PREFIX + json.dumps(ev) + "\n")
    sys.stdout.flush()


def parse_line(line: str):
    if line.startswith(EVENT_PREFIX):
        try:
            return "event", json.loads(line[len(EVENT_PREFIX):])
        except ValueError:
            return "log", line
    return "log", line
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/web/test_events.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add tests/web/test_events.py solaranalysis/web/events.py
git commit -m "feat(web): secret redaction and event framing"
```

---

### Task 14: Runner subprocess (run + test modes)

**Files:**
- Create: `solaranalysis/web/runner.py`
- Test: `tests/web/test_runner.py`

**Interfaces:**
- Produces:
  - `build_app_config(conn, key) -> tuple[AppConfig, dict[int,str]]`: builds an `AppConfig` from enabled plants + settings; returns it plus a map of plant id → name for event labelling.
  - `collect_secrets(cfg) -> list[str]`: every non-empty password/token across plants (for the `Redactor`).
  - `run_analysis_job(paths: Paths, run_id: int) -> int`: the `--run` entry. Loads `.env` from `paths.env_file`; opens the DB; builds config; constructs `SessionStore(paths.session_cache_dir)`; wraps the pipeline `progress` callback to `emit_event` (redacted); on completion writes the report into `paths.output_dir/<stamp>/report.html` via `render_html`/`write_report` with the shared unavailable-section helper; emits `report_written` then `run_complete` with `status` (`success`/`partial`), `skipped`, `plants_summary`, `notes` (incl. `verify_missing_count`). Returns process exit code (0 on report written).
  - `run_test_job(paths: Paths, plant_id: int) -> int`: the `--test` entry. Builds the single adapter via `get_adapter(load_plant_auth(...), SessionStore(...))`, calls `verify_login()`, prints `@@EVENT@@ {"event":"test_result","ok":bool,"error":str|None}`; returns 0 iff ok.
  - `main(argv=None) -> int`: argparse for `--run/--test`, `--run-id`, `--plant-id`, `--db`, `--data-dir`, `--app-dir`.
- Consumes: `events.emit_event/Redactor` (13); `repo` (6–8); `crypto` (3); `paths` (2); `run_pipeline` progress (10); `report.render_html/write_report/append_unavailable_section` (12); `adapters.base.get_adapter` + `verify_login` (11).

Note: all stdout redaction happens **inside** the runner for the event/args it controls; the server applies a second redaction pass over the raw child stream (Task 15) to catch library output the runner never formats.

- [ ] **Step 1: Write the failing test**

`tests/web/test_runner.py`:

```python
import json
from solaranalysis.web import db, repo, crypto, runner
from solaranalysis.web.paths import Paths


def _paths(tmp_path):
    app = tmp_path / "app"; app.mkdir()
    return Paths.create(str(tmp_path / "data"), str(app))


def _seed(paths):
    conn = db.connect(paths.db_path)
    db.init_db(conn)
    key = crypto.load_or_create_key(paths.key_path)
    repo.create_plant(conn, key, {"name": "Good", "platform": "growatt",
                                  "auth_mode": "password", "username": "u",
                                  "password": "pw", "tariff_per_kwh": 0.5})
    return conn, key


def test_build_app_config_from_db(tmp_path):
    paths = _paths(tmp_path)
    conn, key = _seed(paths)
    cfg, names = runner.build_app_config(conn, key)
    assert len(cfg.plants) == 1
    assert cfg.plants[0].auth.username == "u"
    assert cfg.plants[0].auth.password == "pw"
    assert cfg.max_input_tokens == 60000


def test_collect_secrets(tmp_path):
    paths = _paths(tmp_path)
    conn, key = _seed(paths)
    cfg, _ = runner.build_app_config(conn, key)
    assert "pw" in runner.collect_secrets(cfg)


def test_run_job_emits_events_and_writes_report(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    conn, key = _seed(paths)
    conn.close()

    # Stub the pipeline so no browser/network is touched; drive progress + result.
    from solaranalysis.core.schema import PlantData
    def fake_pipeline(cfg, tr, ss, progress=None):
        progress({"event": "plant_start", "plant": "Good"})
        progress({"event": "plant_done", "plant": "Good", "ok": True})
        progress({"event": "analyze_start"})
        return {"report_md": "# Report", "plants": [PlantData(
                    plant_id="g", source_platform="growatt",
                    source_plant_id="1", plant_name="Good")],
                "verify_missing": ["123"], "skipped_plants": []}
    monkeypatch.setattr(runner, "run_pipeline", fake_pipeline)

    rc = runner.run_analysis_job(paths, run_id=1)
    assert rc == 0
    out = capsys.readouterr().out
    events = [json.loads(l[len("@@EVENT@@ "):]) for l in out.splitlines()
              if l.startswith("@@EVENT@@ ")]
    kinds = [e["event"] for e in events]
    assert "run_start" in kinds and "report_written" in kinds
    complete = [e for e in events if e["event"] == "run_complete"][0]
    assert complete["status"] == "success"
    assert complete["notes"]["verify_missing_count"] == 1


def test_run_job_partial_when_skipped(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    conn, key = _seed(paths); conn.close()
    def fake_pipeline(cfg, tr, ss, progress=None):
        return {"report_md": "# R", "plants": [], "verify_missing": [],
                "skipped_plants": [{"name": "Good", "reason": "boom"}]}
    monkeypatch.setattr(runner, "run_pipeline", fake_pipeline)
    runner.run_analysis_job(paths, run_id=1)
    out = capsys.readouterr().out
    complete = [json.loads(l[len("@@EVENT@@ "):]) for l in out.splitlines()
                if "run_complete" in l][0]
    assert complete["status"] == "partial"


def test_run_job_redacts_secret_in_events(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    conn, key = _seed(paths); conn.close()
    def fake_pipeline(cfg, tr, ss, progress=None):
        progress({"event": "plant_done", "plant": "Good", "ok": False,
                  "reason": "auth failed for pw"})
        return {"report_md": "# R", "plants": [], "verify_missing": [],
                "skipped_plants": [{"name": "Good", "reason": "auth failed for pw"}]}
    monkeypatch.setattr(runner, "run_pipeline", fake_pipeline)
    runner.run_analysis_job(paths, run_id=1)
    out = capsys.readouterr().out
    assert "pw" not in out.replace("plant", "").replace("Good", "")
    assert "***" in out


def test_test_job_reports_result(tmp_path, monkeypatch, capsys):
    paths = _paths(tmp_path)
    conn, key = _seed(paths)
    pid = repo.list_plants(conn)[0]["id"]; conn.close()

    class FakeAdapter:
        def verify_login(self): return None
    monkeypatch.setattr(runner, "get_adapter", lambda auth, ss: FakeAdapter())
    rc = runner.run_test_job(paths, plant_id=pid)
    assert rc == 0
    out = capsys.readouterr().out
    res = [json.loads(l[len("@@EVENT@@ "):]) for l in out.splitlines()
           if "test_result" in l][0]
    assert res["ok"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/web/test_runner.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement**

`solaranalysis/web/runner.py`:

```python
from __future__ import annotations
import argparse
import sys
import traceback
from datetime import datetime, timezone

from dotenv import load_dotenv

from ..config import AppConfig, PlantConfig
from ..core.schema import TimeRange
from ..core.session_store import SessionStore
from ..core.report import render_html, write_report, append_unavailable_section
from ..adapters.base import get_adapter
from ..pipeline import run_pipeline
from . import db, repo, crypto, events
from .paths import Paths


def build_app_config(conn, key):
    settings = repo.get_app_settings(conn)
    plants, names = [], {}
    for p in repo.list_plants(conn):
        if not p["enabled"]:
            continue
        auth = repo.load_plant_auth(conn, key, p["id"])
        plants.append(PlantConfig(name=p["name"], auth=auth,
                                  tariff_per_kwh=p["tariff_per_kwh"],
                                  currency=p["currency"]))
        names[p["id"]] = p["name"]
    cfg = AppConfig(plants=plants, model=settings["model"],
                    max_input_tokens=settings["max_input_tokens"],
                    output_language=settings["output_language"])
    return cfg, names


def collect_secrets(cfg: AppConfig) -> list[str]:
    out = []
    for pc in cfg.plants:
        if pc.auth.password:
            out.append(pc.auth.password)
        if pc.auth.token:
            out.append(pc.auth.token)
    return out


def run_analysis_job(paths: Paths, run_id: int) -> int:
    load_dotenv(paths.env_file)
    conn = db.connect(paths.db_path)
    key = crypto.load_or_create_key(paths.key_path)
    run = repo.get_run(conn, run_id)
    time_range = TimeRange(run["time_range"])
    cfg, _ = build_app_config(conn, key)
    red = events.Redactor(collect_secrets(cfg))
    ss = SessionStore(paths.session_cache_dir)

    def progress(ev):
        # Redact free-text fields before they leave the process.
        if "reason" in ev and ev["reason"]:
            ev = {**ev, "reason": red.redact(str(ev["reason"]))}
        events.emit_event(ev)

    events.emit_event({"event": "run_start",
                       "plants": [p.name for p in cfg.plants],
                       "time_range": run["time_range"]})
    try:
        res = run_pipeline(cfg, time_range, ss, progress=progress)
    except Exception as e:
        events.emit_event({"event": "run_complete", "status": "failed",
                           "error": red.redact(f"{e}\n{traceback.format_exc()}")})
        conn.close()
        return 1

    skipped = [{"name": s["name"], "reason": red.redact(str(s["reason"]))}
               for s in res["skipped_plants"]]
    report_md = append_unavailable_section(res["report_md"], skipped)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    subtitle = f"{len(res['plants'])} plants · range {run['time_range']} · {stamp} UTC"
    html = render_html(report_md, "Solar Fleet Analysis", subtitle)
    out_dir = f"{paths.output_dir}/{stamp}"
    write_report(html, out_dir)
    rel = f"output/{stamp}/report.html"
    events.emit_event({"event": "report_written", "path": rel})

    status = "partial" if skipped else "success"
    summary = [{"name": p.plant_name, "ok": True} for p in res["plants"]]
    summary += [{"name": s["name"], "ok": False, "reason": s["reason"]} for s in skipped]
    events.emit_event({"event": "run_complete", "status": status,
                       "report_path": rel, "skipped": skipped,
                       "plants_summary": summary,
                       "notes": {"verify_missing_count": len(res["verify_missing"]),
                                 "series_missing": not any(
                                     p.energy_timeseries for p in res["plants"])}})
    conn.close()
    return 0


def run_test_job(paths: Paths, plant_id: int) -> int:
    conn = db.connect(paths.db_path)
    key = crypto.load_or_create_key(paths.key_path)
    auth = repo.load_plant_auth(conn, key, plant_id)
    red = events.Redactor([auth.password, auth.token] if auth else [])
    ss = SessionStore(paths.session_cache_dir)
    ok, error = True, None
    try:
        adapter = get_adapter(auth, ss)
        adapter.verify_login()
    except Exception as e:
        ok, error = False, red.redact(str(e))
    events.emit_event({"event": "test_result", "ok": ok, "error": error})
    conn.close()
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="solaranalysis.web.runner")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--run-id", type=int)
    ap.add_argument("--plant-id", type=int)
    ap.add_argument("--db", required=True)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--app-dir", required=True)
    args = ap.parse_args(argv)
    paths = Paths.create(args.data_dir, args.app_dir)
    if args.test:
        return run_test_job(paths, args.plant_id)
    return run_analysis_job(paths, args.run_id)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/web/test_runner.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add tests/web/test_runner.py solaranalysis/web/runner.py
git commit -m "feat(web): pipeline runner subprocess (run + test)"
```

---

## Phase 5 — Run manager and scheduler

### Task 15: RunManager core — lock, spawn, stdout pump, SSE fan-out

**Files:**
- Create: `solaranalysis/web/run_manager.py`
- Test: `tests/web/test_run_manager.py`

**Interfaces:**
- Produces `RunManager(paths: Paths, spawn=None)`:
  - `spawn(cmd: list[str]) -> proc` returns an object with `.pid: int`, `.stdout` (iterable of text lines), `.wait() -> int`, `.kill() -> None`. Default uses `subprocess.Popen` (stderr merged into stdout, text mode). Injected in tests.
  - `class Busy(Exception)` with `.active: dict` (`{"kind","id"}`).
  - `start_run(trigger: str, time_range: str) -> int` — raises `Busy` if an operation is active; creates the run row, spawns `python -m solaranalysis.web.runner --run ...`, stores pid, starts the pump thread, returns run id.
  - `subscribe(run_id: int) -> queue.Queue`, `unsubscribe(run_id, q)` — for SSE; queue receives dicts `{"type":"log","line"}`, `{"type":"progress","event"}`, `{"type":"end"}`.
  - `get_progress(run_id) -> dict | None` — latest in-memory `{plants: {name: state}, last_event, status}` for a live run.
  - `active() -> dict | None` — `{"kind","id"}` or None.
  - `_pump(run_id, proc)` (internal): reads stdout, redacts, writes log, broadcasts, resolves exit status, finalizes the run row, releases the lock.
- Consumes: `events` (13), `repo`/`db`/`crypto` (3,6–8), `paths` (2).

- [ ] **Step 1: Write the failing test**

`tests/web/test_run_manager.py`:

```python
import queue
import threading
from solaranalysis.web import db, repo, crypto, run_manager
from solaranalysis.web.events import EVENT_PREFIX
from solaranalysis.web.paths import Paths


def _paths(tmp_path):
    app = tmp_path / "app"; app.mkdir()
    p = Paths.create(str(tmp_path / "data"), str(app))
    conn = db.connect(p.db_path); db.init_db(conn)
    key = crypto.load_or_create_key(p.key_path)
    repo.create_plant(conn, key, {"name": "Good", "platform": "growatt",
                                  "auth_mode": "password", "username": "u",
                                  "password": "sekret"})
    conn.close()
    return p


class FakeProc:
    def __init__(self, lines, code=0):
        self._lines = lines
        self.stdout = iter(lines)
        self.pid = 9999
        self._code = code
        self._done = threading.Event()
    def wait(self):
        self._done.wait(timeout=5)
        return self._code
    def kill(self):
        self._done.set()


def _ev(d):
    import json
    return EVENT_PREFIX + json.dumps(d) + "\n"


def test_start_run_success_finalizes(tmp_path):
    paths = _paths(tmp_path)
    lines = [_ev({"event": "run_start", "plants": ["Good"], "time_range": "30d"}),
             _ev({"event": "plant_done", "plant": "Good", "ok": True}),
             _ev({"event": "report_written", "path": "output/x/report.html"}),
             _ev({"event": "run_complete", "status": "success",
                  "report_path": "output/x/report.html", "skipped": [],
                  "plants_summary": [{"name": "Good", "ok": True}],
                  "notes": {"verify_missing_count": 0}})]
    proc = FakeProc(lines)
    proc._done.set()  # wait() returns immediately after stdout drains
    rm = run_manager.RunManager(paths, spawn=lambda cmd: proc)
    rid = rm.start_run("manual", "30d")
    rm.join(rid, timeout=5)  # test helper: waits for pump thread
    conn = db.connect(paths.db_path)
    run = repo.get_run(conn, rid)
    assert run["status"] == "success"
    assert run["report_path"] == "output/x/report.html"
    assert rm.active() is None


def test_busy_rejects_second_start(tmp_path):
    paths = _paths(tmp_path)
    gate = threading.Event()
    class Blocking(FakeProc):
        def __init__(self): super().__init__([]); 
        def wait(self):
            gate.wait(timeout=5); return 0
    proc = Blocking()
    rm = run_manager.RunManager(paths, spawn=lambda cmd: proc)
    rm.start_run("manual", "30d")
    import pytest
    with pytest.raises(run_manager.Busy) as ei:
        rm.start_run("manual", "30d")
    assert ei.value.active["kind"] == "run"
    gate.set()


def test_subscriber_receives_events_and_end(tmp_path):
    paths = _paths(tmp_path)
    lines = [_ev({"event": "run_start", "plants": ["Good"], "time_range": "30d"}),
             "plain log line\n",
             _ev({"event": "run_complete", "status": "success",
                  "report_path": "output/x/report.html", "skipped": [],
                  "plants_summary": [], "notes": {"verify_missing_count": 0}})]
    proc = FakeProc(lines); proc._done.set()
    rm = run_manager.RunManager(paths, spawn=lambda cmd: proc)
    rid = rm.start_run("manual", "30d")
    q = rm.subscribe(rid)
    rm.join(rid, timeout=5)
    seen = []
    while True:
        try:
            seen.append(q.get_nowait())
        except queue.Empty:
            break
    types = [m["type"] for m in seen]
    assert "end" in types


def test_secret_redacted_in_log_and_stream(tmp_path):
    paths = _paths(tmp_path)
    lines = ["traceback: password was sekret\n",
             _ev({"event": "run_complete", "status": "failed"})]
    proc = FakeProc(lines, code=1); proc._done.set()
    rm = run_manager.RunManager(paths, spawn=lambda cmd: proc)
    rid = rm.start_run("manual", "30d")
    rm.join(rid, timeout=5)
    conn = db.connect(paths.db_path)
    run = repo.get_run(conn, rid)
    log = open(paths.data_dir + "/" + run["log_path"], encoding="utf-8").read()
    assert "sekret" not in log and "***" in log
    assert run["status"] == "failed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/web/test_run_manager.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement**

`solaranalysis/web/run_manager.py`:

```python
from __future__ import annotations
import os
import queue
import subprocess
import sys
import threading
from datetime import datetime, timezone

from . import db, repo, crypto, events
from .paths import Paths


class Busy(Exception):
    def __init__(self, active: dict):
        super().__init__(f"operation active: {active}")
        self.active = active


def _default_spawn(cmd):
    return subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1,
                            encoding="utf-8", errors="replace")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RunManager:
    def __init__(self, paths: Paths, spawn=None):
        self.paths = paths
        self._spawn = spawn or _default_spawn
        self._lock = threading.Lock()
        self._active = None            # {"kind","id","proc","cancel":bool}
        self._threads: dict[int, threading.Thread] = {}
        self._subs: dict[int, set] = {}
        self._progress: dict[int, dict] = {}
        self._subs_lock = threading.Lock()

    # ---- introspection -------------------------------------------------
    def active(self):
        with self._lock:
            if not self._active:
                return None
            return {"kind": self._active["kind"], "id": self._active["id"]}

    def get_progress(self, run_id):
        return self._progress.get(run_id)

    # ---- SSE fan-out ---------------------------------------------------
    def subscribe(self, run_id) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._subs_lock:
            self._subs.setdefault(run_id, set()).add(q)
        return q

    def unsubscribe(self, run_id, q):
        with self._subs_lock:
            self._subs.get(run_id, set()).discard(q)

    def _broadcast(self, run_id, msg):
        with self._subs_lock:
            for q in list(self._subs.get(run_id, set())):
                q.put(msg)

    # ---- start a run ---------------------------------------------------
    def _secrets(self, conn):
        key = crypto.load_or_create_key(self.paths.key_path)
        out = []
        for p in repo.list_plants(conn):
            auth = repo.load_plant_auth(conn, key, p["id"])
            if auth and auth.password:
                out.append(auth.password)
            if auth and auth.token:
                out.append(auth.token)
        return out

    def start_run(self, trigger: str, time_range: str) -> int:
        with self._lock:
            if self._active:
                raise Busy({"kind": self._active["kind"], "id": self._active["id"]})
            conn = db.connect(self.paths.db_path)
            log_rel = ""  # set after we know the id
            rid = repo.create_run(conn, trigger=trigger, time_range=time_range,
                                  log_path="pending", started_at=_now())
            log_rel = f"logs/run-{rid}.log"
            conn.execute("UPDATE runs SET log_path=? WHERE id=?", (log_rel, rid))
            conn.commit()
            cmd = [sys.executable, "-m", "solaranalysis.web.runner", "--run",
                   "--run-id", str(rid), "--db", self.paths.db_path,
                   "--data-dir", self.paths.data_dir, "--app-dir", self.paths.app_dir]
            proc = self._spawn(cmd)
            repo.set_run_pid(conn, rid, proc.pid)
            conn.close()
            self._active = {"kind": "run", "id": rid, "proc": proc, "cancel": False}
            self._progress[rid] = {"plants": {}, "last_event": None, "status": "running"}
            t = threading.Thread(target=self._pump, args=(rid, proc), daemon=True)
            self._threads[rid] = t
            t.start()
            return rid

    # ---- pump ----------------------------------------------------------
    def _apply_event(self, run_id, ev, result):
        prog = self._progress.setdefault(run_id, {"plants": {}, "last_event": None,
                                                  "status": "running"})
        prog["last_event"] = ev
        name = ev.get("plant")
        if ev["event"] == "plant_start" and name:
            prog["plants"][name] = "running"
        elif ev["event"] == "plant_done" and name:
            prog["plants"][name] = "ok" if ev.get("ok") else "failed"
        elif ev["event"] == "run_complete":
            result.update({k: ev.get(k) for k in
                           ("status", "report_path", "skipped",
                            "plants_summary", "notes", "error")})

    def _pump(self, run_id, proc):
        conn = db.connect(self.paths.db_path)
        red = events.Redactor(self._secrets(conn))
        log_path = os.path.join(self.paths.data_dir, f"logs/run-{run_id}.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        result = {"status": None, "report_path": None, "skipped": None,
                  "plants_summary": None, "notes": None, "error": None}
        tail = []
        with open(log_path, "a", encoding="utf-8") as log_fp:
            for raw in proc.stdout:
                line = red.redact(raw.rstrip("\n"))
                log_fp.write(line + "\n"); log_fp.flush()
                tail.append(line)
                del tail[:-50]
                kind, val = events.parse_line(line)
                self._broadcast(run_id, {"type": "log", "line": line})
                if kind == "event":
                    self._apply_event(run_id, val, result)
                    self._broadcast(run_id, {"type": "progress", "event": val})
            code = proc.wait()
        self._finish(run_id, result, code, "\n".join(tail)[-500:], conn)
        conn.close()
        self._broadcast(run_id, {"type": "end"})
        with self._lock:
            if self._active and self._active["id"] == run_id:
                self._active = None

    def _finish(self, run_id, result, code, tail, conn):
        cancelled = bool(self._active and self._active.get("cancel"))
        if cancelled:
            status = "cancelled"
        elif result["status"] in ("success", "partial") and result["report_path"]:
            status = result["status"]
        else:
            status = "failed"
        self._progress.get(run_id, {})["status"] = status
        repo.finalize_run(
            conn, run_id, status=status, finished_at=_now(),
            report_path=result["report_path"],
            plants_summary=result["plants_summary"],
            skipped_plants=result["skipped"], notes=result["notes"],
            error=result["error"] or (None if status != "failed" else tail))

    # ---- test helper: wait for a run's pump thread ---------------------
    def join(self, run_id, timeout=None):
        t = self._threads.get(run_id)
        if t:
            t.join(timeout)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/web/test_run_manager.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add tests/web/test_run_manager.py solaranalysis/web/run_manager.py
git commit -m "feat(web): RunManager lock, subprocess pump, SSE fan-out"
```

---

### Task 16: Cancel, connection test, and startup reconciliation

**Files:**
- Modify: `solaranalysis/web/run_manager.py`
- Test: `tests/web/test_run_manager_cancel.py`

**Interfaces:**
- Produces (on `RunManager`):
  - `cancel(run_id: int) -> bool` — if that run is active, set `cancel=True`, kill the process tree (psutil; children first), return True; else False.
  - `run_test(plant_id: int, timeout_s: float = 95) -> dict` — raises `Busy` if active; spawns `runner --test`, reads the `test_result` event synchronously, records it via `repo.set_plant_test_result`, releases the lock, returns `{"ok":bool,"error":str|None}`. Takes the same global lock as runs.
  - `reconcile_on_startup() -> int` — for each `running` run row: if `runner_pid` is a live process, kill it; mark the row `interrupted`. Returns count reconciled. Called once at server startup.
- Consumes: `psutil`, `repo`.

- [ ] **Step 1: Write the failing test**

`tests/web/test_run_manager_cancel.py`:

```python
import threading
from solaranalysis.web import db, repo, crypto, run_manager
from solaranalysis.web.events import EVENT_PREFIX
from solaranalysis.web.paths import Paths


def _paths(tmp_path):
    app = tmp_path / "app"; app.mkdir()
    p = Paths.create(str(tmp_path / "data"), str(app))
    conn = db.connect(p.db_path); db.init_db(conn); conn.close()
    return p


class KillableProc:
    def __init__(self):
        self.pid = 4242
        self._killed = threading.Event()
        self.stdout = self._gen()
    def _gen(self):
        # Block until killed, yielding nothing (simulates a hung run).
        self._killed.wait(timeout=5)
        return
        yield
    def wait(self):
        self._killed.wait(timeout=5)
        return -9
    def kill(self):
        self._killed.set()


def test_cancel_marks_cancelled(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    proc = KillableProc()
    rm = run_manager.RunManager(paths, spawn=lambda cmd: proc)
    # Avoid real psutil tree-kill; route to proc.kill().
    monkeypatch.setattr(rm, "_kill_tree", lambda pid: proc.kill())
    rid = rm.start_run("manual", "30d")
    assert rm.cancel(rid) is True
    rm.join(rid, timeout=5)
    conn = db.connect(paths.db_path)
    assert repo.get_run(conn, rid)["status"] == "cancelled"


def test_run_test_records_result(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    conn = db.connect(paths.db_path)
    key = crypto.load_or_create_key(paths.key_path)
    pid = repo.create_plant(conn, key, {"name": "G", "platform": "sma",
                                        "auth_mode": "password",
                                        "username": "u", "password": "p"})
    conn.close()

    class TestProc:
        def __init__(self, ok):
            self.pid = 1
            self.stdout = iter([EVENT_PREFIX + '{"event":"test_result","ok":%s,"error":null}\n'
                                % ("true" if ok else "false")])
        def wait(self): return 0
        def kill(self): pass
    rm = run_manager.RunManager(paths, spawn=lambda cmd: TestProc(True))
    res = rm.run_test(pid)
    assert res["ok"] is True
    conn = db.connect(paths.db_path)
    assert repo.get_plant(conn, pid)["last_test_ok"] is True


def test_reconcile_marks_dead_running_as_interrupted(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    conn = db.connect(paths.db_path)
    rid = repo.create_run(conn, trigger="manual", time_range="30d",
                          log_path="logs/run-1.log", started_at="2026-07-04T00:00:00")
    repo.set_run_pid(conn, rid, 999999)  # not a live pid
    conn.close()
    rm = run_manager.RunManager(paths)
    monkeypatch.setattr(run_manager, "_pid_alive", lambda pid: False)
    n = rm.reconcile_on_startup()
    assert n == 1
    conn = db.connect(paths.db_path)
    assert repo.get_run(conn, rid)["status"] == "interrupted"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/web/test_run_manager_cancel.py -v`
Expected: FAIL (methods not defined).

- [ ] **Step 3: Implement**

Append helpers at module level in `run_manager.py`:

```python
def _pid_alive(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        return False
```

Add methods to `RunManager`:

```python
    def _kill_tree(self, pid: int) -> None:
        try:
            import psutil
            parent = psutil.Process(pid)
            for child in parent.children(recursive=True):
                try:
                    child.kill()
                except Exception:
                    pass
            parent.kill()
        except Exception:
            pass

    def cancel(self, run_id: int) -> bool:
        with self._lock:
            if not self._active or self._active["id"] != run_id \
                    or self._active["kind"] != "run":
                return False
            self._active["cancel"] = True
            proc = self._active["proc"]
        self._kill_tree(proc.pid)
        try:
            proc.kill()
        except Exception:
            pass
        return True

    def run_test(self, plant_id: int, timeout_s: float = 95) -> dict:
        with self._lock:
            if self._active:
                raise Busy({"kind": self._active["kind"], "id": self._active["id"]})
            cmd = [sys.executable, "-m", "solaranalysis.web.runner", "--test",
                   "--plant-id", str(plant_id), "--db", self.paths.db_path,
                   "--data-dir", self.paths.data_dir, "--app-dir", self.paths.app_dir]
            proc = self._spawn(cmd)
            self._active = {"kind": "test", "id": plant_id, "proc": proc, "cancel": False}
        result = {"ok": False, "error": "no result"}
        try:
            for raw in proc.stdout:
                kind, val = events.parse_line(raw.rstrip("\n"))
                if kind == "event" and val.get("event") == "test_result":
                    result = {"ok": bool(val.get("ok")), "error": val.get("error")}
            proc.wait()
        finally:
            conn = db.connect(self.paths.db_path)
            repo.set_plant_test_result(conn, plant_id, ok=result["ok"],
                                       error=result["error"], at=_now())
            conn.close()
            with self._lock:
                self._active = None
        return result

    def reconcile_on_startup(self) -> int:
        conn = db.connect(self.paths.db_path)
        n = 0
        for run in repo.running_runs(conn):
            pid = run.get("runner_pid")
            if pid and _pid_alive(pid):
                self._kill_tree(pid)
            repo.mark_interrupted(conn, run["id"], finished_at=_now())
            n += 1
        conn.close()
        return n
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/web/test_run_manager_cancel.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add tests/web/test_run_manager_cancel.py solaranalysis/web/run_manager.py
git commit -m "feat(web): cancel, connection test, startup reconciliation"
```

---

### Task 17: Scheduler

**Files:**
- Create: `solaranalysis/web/scheduler.py`
- Test: `tests/web/test_scheduler.py`

**Interfaces:**
- Produces `ScheduleService(paths: Paths, run_manager, scheduler=None)`:
  - `build_jobs() -> list[dict]` — reads enabled schedule rows and returns the cron-job specs `[{"id","day_of_week","hour","minute","time_range"}]` (parses `days_of_week` CSV and `HH:MM`). Pure/testable without APScheduler.
  - `fire(time_range: str) -> None` — calls `run_manager.start_run("scheduled", time_range)`; on `Busy`, logs a skip line and returns (no run row).
  - `reload() -> None` — clears and re-adds APScheduler cron jobs from `build_jobs()`.
  - `start()` / `shutdown()`.
- Consumes: `apscheduler.schedulers.background.BackgroundScheduler`, `repo`, `run_manager` (15).

- [ ] **Step 1: Write the failing test**

`tests/web/test_scheduler.py`:

```python
from solaranalysis.web import db, repo, scheduler
from solaranalysis.web.paths import Paths
from solaranalysis.web.run_manager import Busy


def _paths(tmp_path):
    app = tmp_path / "app"; app.mkdir()
    p = Paths.create(str(tmp_path / "data"), str(app))
    conn = db.connect(p.db_path); db.init_db(conn)
    repo.create_schedule(conn, {"time_of_day": "06:30", "days_of_week": "0,4",
                                "time_range": "30d", "enabled": True})
    repo.create_schedule(conn, {"time_of_day": "23:00", "days_of_week": "1",
                                "time_range": "all", "enabled": False})
    conn.close()
    return p


class FakeRM:
    def __init__(self, busy=False):
        self.calls = []
        self._busy = busy
    def start_run(self, trigger, time_range):
        if self._busy:
            raise Busy({"kind": "run", "id": 1})
        self.calls.append((trigger, time_range))
        return 7


def test_build_jobs_only_enabled(tmp_path):
    paths = _paths(tmp_path)
    svc = scheduler.ScheduleService(paths, FakeRM(), scheduler=object())
    jobs = svc.build_jobs()
    assert len(jobs) == 1
    j = jobs[0]
    assert j["hour"] == 6 and j["minute"] == 30
    assert j["day_of_week"] == "0,4" and j["time_range"] == "30d"


def test_fire_starts_run(tmp_path):
    paths = _paths(tmp_path)
    rm = FakeRM()
    svc = scheduler.ScheduleService(paths, rm, scheduler=object())
    svc.fire("30d")
    assert rm.calls == [("scheduled", "30d")]


def test_fire_skips_when_busy(tmp_path):
    paths = _paths(tmp_path)
    rm = FakeRM(busy=True)
    svc = scheduler.ScheduleService(paths, rm, scheduler=object())
    svc.fire("30d")  # must not raise
    assert rm.calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/web/test_scheduler.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement**

`solaranalysis/web/scheduler.py`:

```python
from __future__ import annotations
import logging

from . import db, repo
from .paths import Paths
from .run_manager import Busy

log = logging.getLogger("solar.scheduler")


class ScheduleService:
    def __init__(self, paths: Paths, run_manager, scheduler=None):
        self.paths = paths
        self.rm = run_manager
        self._sched = scheduler  # APScheduler instance; injected/lazy

    def build_jobs(self) -> list[dict]:
        conn = db.connect(self.paths.db_path)
        jobs = []
        for s in repo.list_schedules(conn):
            if not s["enabled"]:
                continue
            hh, mm = s["time_of_day"].split(":")
            jobs.append({"id": s["id"], "day_of_week": s["days_of_week"],
                         "hour": int(hh), "minute": int(mm),
                         "time_range": s["time_range"]})
        conn.close()
        return jobs

    def fire(self, time_range: str) -> None:
        try:
            self.rm.start_run("scheduled", time_range)
        except Busy:
            log.info("scheduled run (%s) skipped: an operation is active", time_range)

    def _ensure_sched(self):
        if self._sched is None:
            from apscheduler.schedulers.background import BackgroundScheduler
            self._sched = BackgroundScheduler()
        return self._sched

    def reload(self) -> None:
        sched = self._ensure_sched()
        for job in list(sched.get_jobs()):
            job.remove()
        for spec in self.build_jobs():
            sched.add_job(self.fire, "cron", args=[spec["time_range"]],
                          day_of_week=spec["day_of_week"], hour=spec["hour"],
                          minute=spec["minute"], id=f"sched-{spec['id']}",
                          misfire_grace_time=300, coalesce=True)

    def start(self) -> None:
        sched = self._ensure_sched()
        self.reload()
        if not sched.running:
            sched.start()

    def shutdown(self) -> None:
        if self._sched and self._sched.running:
            self._sched.shutdown(wait=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/web/test_scheduler.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add tests/web/test_scheduler.py solaranalysis/web/scheduler.py
git commit -m "feat(web): APScheduler-backed schedule service"
```

---

## Phase 6 — HTTP API

Shared conventions for all route tasks:

- `create_app(paths, run_manager=None, schedule_service=None) -> FastAPI` builds the app, stores collaborators on `app.state`, generates & logs a setup token when `setup_required`, and registers a `@app.on_event("startup")` that calls `run_manager.reconcile_on_startup()` and `schedule_service.start()` (both optional/None-safe).
- A per-request dependency `db_dep` yields a `db.connect(paths.db_path)` connection and closes it.
- Middleware `auth_and_csrf` enforces: (a) cookie required on `/api/*` except `auth/status|login|setup`; (b) header `X-Solar-CSRF` required on any `POST/PUT/DELETE` to `/api/*`.
- Cookie name `solar_session`; `httponly=True, samesite="lax"`, 30-day `max_age`.
- Session signing key = `crypto.load_or_create_key(paths.key_path)`; epoch read from the DB per request (authoritative).

### Task 18: App factory, auth middleware, auth routes

**Files:**
- Create: `solaranalysis/web/app.py`
- Create: `solaranalysis/web/routes/auth.py`
- Test: `tests/web/test_api_auth.py`

**Interfaces:**
- Produces: `create_app(...)` (above); `db_dep`; middleware; auth router with `GET /api/auth/status`, `POST /api/auth/setup`, `POST /api/auth/login`, `POST /api/auth/logout`, `PUT /api/auth/password`.
- Consumes: `auth` (4), `repo` (6), `crypto` (3), `run_manager`/`scheduler` (optional).

- [ ] **Step 1: Write the failing test**

`tests/web/test_api_auth.py`:

```python
import pytest
from fastapi.testclient import TestClient
from solaranalysis.web import db, repo, crypto
from solaranalysis.web.app import create_app
from solaranalysis.web.paths import Paths

CSRF = {"X-Solar-CSRF": "1"}


def _client(tmp_path):
    app_dir = tmp_path / "app"; app_dir.mkdir()
    paths = Paths.create(str(tmp_path / "data"), str(app_dir))
    conn = db.connect(paths.db_path); db.init_db(conn); conn.close()
    app = create_app(paths)
    return TestClient(app), paths


def _setup_token(paths):
    # The token is generated in create_app; re-derive by reading its hash is not
    # possible, so tests set a known token directly.
    conn = db.connect(paths.db_path)
    import hashlib
    repo.set_setup_token_hash(conn, hashlib.sha256(b"tok123").hexdigest())
    conn.close()


def test_status_before_setup(tmp_path):
    client, paths = _client(tmp_path)
    r = client.get("/api/auth/status")
    assert r.status_code == 200
    assert r.json() == {"setup_required": True, "authenticated": False}


def test_setup_requires_token(tmp_path):
    client, paths = _client(tmp_path)
    _setup_token(paths)
    bad = client.post("/api/auth/setup", json={"token": "wrong", "password": "pw"}, headers=CSRF)
    assert bad.status_code == 403
    ok = client.post("/api/auth/setup", json={"token": "tok123", "password": "pw"}, headers=CSRF)
    assert ok.status_code == 200
    # second setup rejected
    again = client.post("/api/auth/setup", json={"token": "tok123", "password": "pw"}, headers=CSRF)
    assert again.status_code == 409


def test_login_logout_flow(tmp_path):
    client, paths = _client(tmp_path)
    _setup_token(paths)
    client.post("/api/auth/setup", json={"token": "tok123", "password": "pw"}, headers=CSRF)
    assert client.post("/api/auth/login", json={"password": "nope"}, headers=CSRF).status_code == 401
    r = client.post("/api/auth/login", json={"password": "pw"}, headers=CSRF)
    assert r.status_code == 200
    assert client.get("/api/auth/status").json()["authenticated"] is True
    client.post("/api/auth/logout", headers=CSRF)
    assert client.get("/api/auth/status").json()["authenticated"] is False


def test_protected_route_requires_cookie(tmp_path):
    client, paths = _client(tmp_path)
    # /api/plants is registered later; use auth/password which requires auth.
    r = client.put("/api/auth/password", json={"old": "a", "new": "b"}, headers=CSRF)
    assert r.status_code == 401


def test_csrf_header_required_on_mutation(tmp_path):
    client, paths = _client(tmp_path)
    _setup_token(paths)
    # missing CSRF header -> 403 even though route is public
    r = client.post("/api/auth/setup", json={"token": "tok123", "password": "pw"})
    assert r.status_code == 403


def test_password_change_invalidates_session(tmp_path):
    client, paths = _client(tmp_path)
    _setup_token(paths)
    client.post("/api/auth/setup", json={"token": "tok123", "password": "pw"}, headers=CSRF)
    client.post("/api/auth/login", json={"password": "pw"}, headers=CSRF)
    r = client.put("/api/auth/password", json={"old": "pw", "new": "pw2"}, headers=CSRF)
    assert r.status_code == 200
    # old cookie now fails (epoch bumped)
    assert client.get("/api/auth/status").json()["authenticated"] is False


def test_login_rate_limited(tmp_path):
    client, paths = _client(tmp_path)
    _setup_token(paths)
    client.post("/api/auth/setup", json={"token": "tok123", "password": "pw"}, headers=CSRF)
    for _ in range(5):
        client.post("/api/auth/login", json={"password": "x"}, headers=CSRF)
    r = client.post("/api/auth/login", json={"password": "pw"}, headers=CSRF)
    assert r.status_code == 429
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/web/test_api_auth.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3a: Implement the app factory**

`solaranalysis/web/app.py`:

```python
from __future__ import annotations
import hashlib
import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import db, repo, crypto, auth as authmod
from .paths import Paths

log = logging.getLogger("solar.web")

COOKIE = "solar_session"
_PUBLIC = {"/api/auth/status", "/api/auth/login", "/api/auth/setup"}


def db_dep_factory(paths: Paths):
    def _dep():
        conn = db.connect(paths.db_path)
        try:
            yield conn
        finally:
            conn.close()
    return _dep


def _authenticated(request: Request) -> bool:
    paths: Paths = request.app.state.paths
    cookie = request.cookies.get(COOKIE)
    if not cookie:
        return False
    conn = db.connect(paths.db_path)
    try:
        epoch = repo.get_session_epoch(conn)
    finally:
        conn.close()
    return authmod.check_cookie(request.app.state.key, cookie, epoch)


def create_app(paths: Paths, run_manager=None, schedule_service=None) -> FastAPI:
    app = FastAPI()
    app.state.paths = paths
    app.state.key = crypto.load_or_create_key(paths.key_path)
    app.state.rate_limiter = authmod.RateLimiter(max_fails=5, window_s=60)
    app.state.run_manager = run_manager
    app.state.schedule_service = schedule_service
    app.state.db_dep = db_dep_factory(paths)

    # First-boot: generate + log a setup token if none exists yet.
    conn = db.connect(paths.db_path)
    if repo.setup_required(conn) and repo.get_setup_token_hash(conn) is None:
        token = os.urandom(16).hex()
        repo.set_setup_token_hash(conn, hashlib.sha256(token.encode()).hexdigest())
        log.warning("SETUP TOKEN (enter in the web setup screen): %s", token)
    conn.close()

    @app.middleware("http")
    async def auth_and_csrf(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/"):
            if request.method in ("POST", "PUT", "DELETE"):
                if request.headers.get(authmod.CSRF_HEADER) is None:
                    return JSONResponse({"detail": "CSRF header required"}, status_code=403)
            if path not in _PUBLIC and not _authenticated(request):
                return JSONResponse({"detail": "authentication required"}, status_code=401)
        return await call_next(request)

    from .routes.auth import router as auth_router
    app.include_router(auth_router, prefix="/api/auth")

    @app.on_event("startup")
    def _startup():
        if app.state.run_manager:
            app.state.run_manager.reconcile_on_startup()
        if app.state.schedule_service:
            app.state.schedule_service.start()

    return app
```

- [ ] **Step 3b: Implement the auth routes**

`solaranalysis/web/routes/auth.py`:

```python
from __future__ import annotations
import hashlib
import hmac

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import repo, auth as authmod
from ..app import COOKIE

router = APIRouter()


class SetupBody(BaseModel):
    token: str
    password: str


class LoginBody(BaseModel):
    password: str


class PasswordBody(BaseModel):
    old: str
    new: str


def _conn(request: Request):
    return request.app.state.db_dep


def _set_cookie(resp: Response, request: Request, conn):
    epoch = repo.get_session_epoch(conn)
    cookie = authmod.make_cookie(request.app.state.key, epoch)
    resp.set_cookie(COOKIE, cookie, httponly=True, samesite="lax",
                    max_age=30 * 24 * 3600)


@router.get("/status")
def status(request: Request, conn=Depends(lambda r=None: None)):
    p = request.app.state.paths
    from .. import db
    c = db.connect(p.db_path)
    try:
        setup_required = repo.setup_required(c)
    finally:
        c.close()
    from ..app import _authenticated
    return {"setup_required": setup_required, "authenticated": _authenticated(request)}


@router.post("/setup")
def setup(body: SetupBody, request: Request):
    p = request.app.state.paths
    from .. import db
    c = db.connect(p.db_path)
    try:
        if not repo.setup_required(c):
            return JSONResponse({"detail": "already set up"}, status_code=409)
        stored = repo.get_setup_token_hash(c)
        given = hashlib.sha256(body.token.encode()).hexdigest()
        if not stored or not hmac.compare_digest(stored, given):
            return JSONResponse({"detail": "invalid setup token"}, status_code=403)
        repo.set_password_hash(c, authmod.hash_password(body.password))
        repo.clear_setup_token(c)
        resp = JSONResponse({"ok": True})
        _set_cookie(resp, request, c)
        return resp
    finally:
        c.close()


@router.post("/login")
def login(body: LoginBody, request: Request):
    ip = request.client.host if request.client else "?"
    rl = request.app.state.rate_limiter
    if rl.is_blocked(ip):
        return JSONResponse({"detail": "too many attempts"}, status_code=429)
    p = request.app.state.paths
    from .. import db
    c = db.connect(p.db_path)
    try:
        h = repo.get_password_hash(c)
        if not h or not authmod.verify_password(body.password, h):
            rl.record_failure(ip)
            return JSONResponse({"detail": "invalid password"}, status_code=401)
        rl.reset(ip)
        resp = JSONResponse({"ok": True})
        _set_cookie(resp, request, c)
        return resp
    finally:
        c.close()


@router.post("/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp


@router.put("/password")
def change_password(body: PasswordBody, request: Request):
    p = request.app.state.paths
    from .. import db
    c = db.connect(p.db_path)
    try:
        h = repo.get_password_hash(c)
        if not h or not authmod.verify_password(body.old, h):
            return JSONResponse({"detail": "wrong current password"}, status_code=403)
        repo.set_password_hash(c, authmod.hash_password(body.new))
        repo.bump_session_epoch(c)  # invalidate all existing cookies
        resp = JSONResponse({"ok": True})
        _set_cookie(resp, request, c)  # re-issue for the caller at the new epoch
        return resp
    finally:
        c.close()
```

(Note: `status` uses a no-op `Depends` only to keep a uniform signature; the real DB access is inline. Simplify if your FastAPI version warns.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/web/test_api_auth.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add tests/web/test_api_auth.py solaranalysis/web/app.py solaranalysis/web/routes/auth.py
git commit -m "feat(web): app factory, auth middleware, auth routes"
```

---

### Task 19: Plants and settings routes

**Files:**
- Create: `solaranalysis/web/routes/plants.py`
- Create: `solaranalysis/web/routes/settings.py`
- Modify: `solaranalysis/web/app.py` (include the two routers)
- Test: `tests/web/test_api_plants.py`

**Interfaces:**
- Produces:
  - Plants router: `GET /api/plants`, `POST /api/plants`, `GET /api/plants/{id}`, `PUT /api/plants/{id}`, `DELETE /api/plants/{id}`, `POST /api/plants/{id}/test`.
  - `validate_plant(data: dict, existing: dict | None) -> None` in `plants.py` — raises `ValueError` on the §11 rules (platform enum, name non-empty, token only for growatt, required secrets on create, mode/platform-switch rules on update).
  - Settings router: `GET /api/settings`, `PUT /api/settings`.
- Consumes: `repo` (7), `run_manager.run_test`/`Busy` (16).

- [ ] **Step 1: Write the failing test**

`tests/web/test_api_plants.py`:

```python
import hashlib
from fastapi.testclient import TestClient
from solaranalysis.web import db, repo
from solaranalysis.web.app import create_app
from solaranalysis.web.paths import Paths

CSRF = {"X-Solar-CSRF": "1"}


class FakeRM:
    def __init__(self): self.tested = None
    def run_test(self, plant_id, timeout_s=95):
        self.tested = plant_id
        return {"ok": True, "error": None}


def _client(tmp_path, rm=None):
    app_dir = tmp_path / "app"; app_dir.mkdir()
    paths = Paths.create(str(tmp_path / "data"), str(app_dir))
    conn = db.connect(paths.db_path); db.init_db(conn)
    repo.set_setup_token_hash(conn, hashlib.sha256(b"t").hexdigest())
    conn.close()
    app = create_app(paths, run_manager=rm)
    client = TestClient(app)
    client.post("/api/auth/setup", json={"token": "t", "password": "pw"}, headers=CSRF)
    return client, paths


def test_create_list_hides_secrets(tmp_path):
    client, _ = _client(tmp_path)
    r = client.post("/api/plants", headers=CSRF, json={
        "name": "Roof", "platform": "solaredge", "auth_mode": "password",
        "username": "a@b.com", "password": "pw", "tariff_per_kwh": 0.5,
        "currency": "ILS"})
    assert r.status_code == 201
    lst = client.get("/api/plants").json()
    assert lst[0]["has_password"] is True
    assert "password" not in lst[0]


def test_create_rejects_token_for_non_growatt(tmp_path):
    client, _ = _client(tmp_path)
    r = client.post("/api/plants", headers=CSRF, json={
        "name": "X", "platform": "sma", "auth_mode": "token", "token": "t"})
    assert r.status_code == 422


def test_create_requires_secret(tmp_path):
    client, _ = _client(tmp_path)
    r = client.post("/api/plants", headers=CSRF, json={
        "name": "X", "platform": "sma", "auth_mode": "password", "username": "u"})
    assert r.status_code == 422


def test_update_blank_password_keeps(tmp_path):
    client, paths = _client(tmp_path)
    pid = client.post("/api/plants", headers=CSRF, json={
        "name": "G", "platform": "growatt", "auth_mode": "password",
        "username": "u", "password": "orig"}).json()["id"]
    client.put(f"/api/plants/{pid}", headers=CSRF,
               json={"username": "u2", "password": ""})
    p = client.get(f"/api/plants/{pid}").json()
    assert p["username"] == "u2" and p["has_password"] is True


def test_delete(tmp_path):
    client, _ = _client(tmp_path)
    pid = client.post("/api/plants", headers=CSRF, json={
        "name": "G", "platform": "sma", "auth_mode": "password",
        "username": "u", "password": "p"}).json()["id"]
    assert client.delete(f"/api/plants/{pid}", headers=CSRF).status_code == 200
    assert client.get("/api/plants").json() == []


def test_test_endpoint_calls_run_manager(tmp_path):
    rm = FakeRM()
    client, _ = _client(tmp_path, rm=rm)
    pid = client.post("/api/plants", headers=CSRF, json={
        "name": "G", "platform": "sma", "auth_mode": "password",
        "username": "u", "password": "p"}).json()["id"]
    r = client.post(f"/api/plants/{pid}/test", headers=CSRF)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert rm.tested == pid


def test_test_endpoint_409_when_disabled(tmp_path):
    rm = FakeRM()
    client, _ = _client(tmp_path, rm=rm)
    pid = client.post("/api/plants", headers=CSRF, json={
        "name": "G", "platform": "sma", "auth_mode": "password",
        "username": "u", "password": "p", "enabled": False}).json()["id"]
    r = client.post(f"/api/plants/{pid}/test", headers=CSRF)
    assert r.status_code == 409


def test_settings_get_put(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get("/api/settings").json()["output_language"] == "en"
    client.put("/api/settings", headers=CSRF,
               json={"model": None, "max_input_tokens": 1000, "output_language": "he"})
    assert client.get("/api/settings").json()["max_input_tokens"] == 1000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/web/test_api_plants.py -v`
Expected: FAIL (routers not registered).

- [ ] **Step 3a: Implement validation + plants router**

`solaranalysis/web/routes/plants.py`:

```python
from __future__ import annotations
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import repo, db

router = APIRouter()

_PLATFORMS = {"solaredge", "growatt", "sma"}


class PlantBody(BaseModel):
    name: str | None = None
    platform: str | None = None
    auth_mode: str | None = None
    username: str | None = None
    password: str | None = None
    token: str | None = None
    tariff_per_kwh: float | None = None
    currency: str | None = None
    enabled: bool | None = None


def validate_plant(data: dict, existing: dict | None) -> None:
    is_create = existing is None
    platform = data.get("platform") or (existing or {}).get("platform")
    auth_mode = data.get("auth_mode") or (existing or {}).get("auth_mode") or "password"
    if platform not in _PLATFORMS:
        raise ValueError(f"platform must be one of {sorted(_PLATFORMS)}")
    if is_create and not (data.get("name") or "").strip():
        raise ValueError("name is required")
    if auth_mode == "token" and platform != "growatt":
        raise ValueError("token mode is only valid for growatt")
    if is_create:
        if auth_mode == "password" and not (data.get("username") and data.get("password")):
            raise ValueError("password mode requires username and password")
        if auth_mode == "token" and not data.get("token"):
            raise ValueError("token mode requires a token")
    else:
        has_pw = existing["has_password"] or bool(data.get("password"))
        has_tok = existing["has_token"] or bool(data.get("token"))
        if auth_mode == "password" and not (
                (data.get("username") or existing["username"]) and has_pw):
            raise ValueError("password mode requires username and a stored/new password")
        if auth_mode == "token" and platform == "growatt" and not has_tok:
            raise ValueError("token mode requires a stored/new token")


def _conn(request: Request):
    yield from request.app.state.db_dep()


@router.get("")
def list_plants(conn=Depends(_conn)):
    return repo.list_plants(conn)


@router.post("")
def create_plant(body: PlantBody, request: Request, conn=Depends(_conn)):
    data = body.model_dump(exclude_none=True)
    try:
        validate_plant(data, None)
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=422)
    pid = repo.create_plant(conn, request.app.state.key, data)
    return JSONResponse({"id": pid}, status_code=201)


@router.get("/{pid}")
def get_plant(pid: int, conn=Depends(_conn)):
    p = repo.get_plant(conn, pid)
    if not p:
        return JSONResponse({"detail": "not found"}, status_code=404)
    return p


@router.put("/{pid}")
def update_plant(pid: int, body: PlantBody, request: Request, conn=Depends(_conn)):
    existing = repo.get_plant(conn, pid)
    if not existing:
        return JSONResponse({"detail": "not found"}, status_code=404)
    data = body.model_dump(exclude_unset=True)
    try:
        validate_plant(data, existing)
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=422)
    repo.update_plant(conn, request.app.state.key, pid, data)
    return {"ok": True}


@router.delete("/{pid}")
def delete_plant(pid: int, conn=Depends(_conn)):
    repo.delete_plant(conn, pid)
    return {"ok": True}


@router.post("/{pid}/test")
def test_plant(pid: int, request: Request, conn=Depends(_conn)):
    p = repo.get_plant(conn, pid)
    if not p:
        return JSONResponse({"detail": "not found"}, status_code=404)
    if not p["enabled"]:
        return JSONResponse({"detail": "plant is disabled"}, status_code=409)
    rm = request.app.state.run_manager
    from ..run_manager import Busy
    try:
        return rm.run_test(pid)
    except Busy as b:
        return JSONResponse({"detail": "busy", "active": b.active}, status_code=409)
```

- [ ] **Step 3b: Implement settings router**

`solaranalysis/web/routes/settings.py`:

```python
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
    reload_needed = False
    repo.set_app_settings(conn, body.model, body.max_input_tokens, body.output_language)
    return {"ok": True}
```

- [ ] **Step 3c: Register routers**

In `solaranalysis/web/app.py`, after the auth router include, add:

```python
    from .routes.plants import router as plants_router
    from .routes.settings import router as settings_router
    app.include_router(plants_router, prefix="/api/plants")
    app.include_router(settings_router, prefix="/api/settings")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/web/test_api_plants.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add tests/web/test_api_plants.py solaranalysis/web/routes/plants.py solaranalysis/web/routes/settings.py solaranalysis/web/app.py
git commit -m "feat(web): plants and settings routes"
```

---

### Task 20: Schedules routes and one-time import

**Files:**
- Create: `solaranalysis/web/routes/schedules.py`
- Create: `solaranalysis/web/importer.py`
- Create: `solaranalysis/web/routes/imports.py`
- Modify: `solaranalysis/web/app.py`
- Test: `tests/web/test_api_schedules.py`, `tests/web/test_importer.py`

**Interfaces:**
- Produces:
  - Schedules router: `GET/POST /api/schedules`, `PUT/DELETE /api/schedules/{id}`. After any mutation, calls `schedule_service.reload()` if present.
  - `importer.import_config(conn, key, config_yaml: str, env_file: str) -> dict` — parses via `load_config`, upserts plants by name, writes settings; returns `{"created":[names], "updated":[names], "secrets":{name:{"password":bool,"token":bool}}, "settings":{...}, "error":str|None}`.
  - Import router: `POST /api/import` → runs the import against `paths.config_yaml`/`paths.env_file`; returns the summary (or `{"error":...}` with 400 on parse failure).
- Consumes: `repo` (7,8), `load_config` from `solaranalysis.config`.

- [ ] **Step 1: Write the failing tests**

`tests/web/test_importer.py`:

```python
from solaranalysis.web import db, crypto, repo, importer


def _ctx(tmp_path):
    c = db.connect(str(tmp_path / "app.db")); db.init_db(c)
    key = crypto.load_or_create_key(str(tmp_path / "secret.key"))
    return c, key


def _write_cfg(tmp_path):
    (tmp_path / ".env").write_text("SE_USER=a@b.com\nSE_PASS=pw\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        "model: null\nmax_input_tokens: 1234\noutput_language: he\n"
        "plants:\n  - name: Roof\n    auth:\n      platform: solaredge\n"
        "      mode: password\n      username: ${SE_USER}\n      password: ${SE_PASS}\n"
        "    tariff_per_kwh: 0.5\n    currency: ILS\n", encoding="utf-8")


def test_import_creates_plants_and_settings(tmp_path):
    c, key = _ctx(tmp_path)
    _write_cfg(tmp_path)
    summary = importer.import_config(c, key, str(tmp_path / "config.yaml"),
                                     str(tmp_path / ".env"))
    assert summary["created"] == ["Roof"]
    assert summary["secrets"]["Roof"]["password"] is True
    assert repo.get_app_settings(c)["max_input_tokens"] == 1234
    auth = repo.load_plant_auth(c, key, repo.list_plants(c)[0]["id"])
    assert auth.password == "pw"


def test_import_is_idempotent_updates(tmp_path):
    c, key = _ctx(tmp_path)
    _write_cfg(tmp_path)
    importer.import_config(c, key, str(tmp_path / "config.yaml"), str(tmp_path / ".env"))
    summary = importer.import_config(c, key, str(tmp_path / "config.yaml"), str(tmp_path / ".env"))
    assert summary["updated"] == ["Roof"]
    assert len(repo.list_plants(c)) == 1


def test_import_reports_missing_env(tmp_path):
    c, key = _ctx(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        "plants:\n  - name: X\n    auth:\n      platform: growatt\n"
        "      mode: password\n      username: ${NOPE}\n      password: p\n",
        encoding="utf-8")
    summary = importer.import_config(c, key, str(tmp_path / "config.yaml"),
                                     str(tmp_path / ".env"))
    assert summary["error"] and "NOPE" in summary["error"]
```

`tests/web/test_api_schedules.py`:

```python
import hashlib
from fastapi.testclient import TestClient
from solaranalysis.web import db, repo
from solaranalysis.web.app import create_app
from solaranalysis.web.paths import Paths

CSRF = {"X-Solar-CSRF": "1"}


class FakeSched:
    def __init__(self): self.reloads = 0
    def reload(self): self.reloads += 1
    def start(self): pass


def _client(tmp_path, sched=None):
    app_dir = tmp_path / "app"; app_dir.mkdir()
    paths = Paths.create(str(tmp_path / "data"), str(app_dir))
    conn = db.connect(paths.db_path); db.init_db(conn)
    repo.set_setup_token_hash(conn, hashlib.sha256(b"t").hexdigest())
    conn.close()
    app = create_app(paths, schedule_service=sched)
    client = TestClient(app)
    client.post("/api/auth/setup", json={"token": "t", "password": "pw"}, headers=CSRF)
    return client


def test_schedule_crud_and_reload(tmp_path):
    sched = FakeSched()
    client = _client(tmp_path, sched=sched)
    r = client.post("/api/schedules", headers=CSRF, json={
        "time_of_day": "06:00", "days_of_week": "0,1,2,3,4",
        "time_range": "30d", "enabled": True})
    assert r.status_code == 201
    sid = r.json()["id"]
    assert len(client.get("/api/schedules").json()) == 1
    client.put(f"/api/schedules/{sid}", headers=CSRF, json={"enabled": False})
    assert client.get("/api/schedules").json()[0]["enabled"] is False
    client.delete(f"/api/schedules/{sid}", headers=CSRF)
    assert client.get("/api/schedules").json() == []
    assert sched.reloads == 3  # create, update, delete each reload
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/web/test_importer.py tests/web/test_api_schedules.py -v`
Expected: FAIL (modules/routes not found).

- [ ] **Step 3a: Implement the importer**

`solaranalysis/web/importer.py`:

```python
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
```

- [ ] **Step 3b: Implement the schedules router**

`solaranalysis/web/routes/schedules.py`:

```python
from __future__ import annotations
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import repo

router = APIRouter()


class ScheduleBody(BaseModel):
    time_of_day: str | None = None
    days_of_week: str | None = None
    time_range: str | None = None
    enabled: bool | None = None


def _conn(request: Request):
    yield from request.app.state.db_dep()


def _reload(request: Request):
    svc = request.app.state.schedule_service
    if svc:
        svc.reload()


@router.get("")
def list_schedules(conn=Depends(_conn)):
    return repo.list_schedules(conn)


@router.post("")
def create_schedule(body: ScheduleBody, request: Request, conn=Depends(_conn)):
    data = body.model_dump(exclude_none=True)
    for req in ("time_of_day", "days_of_week", "time_range"):
        if req not in data:
            return JSONResponse({"detail": f"{req} required"}, status_code=422)
    sid = repo.create_schedule(conn, data)
    _reload(request)
    return JSONResponse({"id": sid}, status_code=201)


@router.put("/{sid}")
def update_schedule(sid: int, body: ScheduleBody, request: Request, conn=Depends(_conn)):
    try:
        repo.update_schedule(conn, sid, body.model_dump(exclude_unset=True))
    except KeyError:
        return JSONResponse({"detail": "not found"}, status_code=404)
    _reload(request)
    return {"ok": True}


@router.delete("/{sid}")
def delete_schedule(sid: int, request: Request, conn=Depends(_conn)):
    repo.delete_schedule(conn, sid)
    _reload(request)
    return {"ok": True}
```

- [ ] **Step 3c: Implement the import router**

`solaranalysis/web/routes/imports.py`:

```python
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
        return JSONResponse(summary, status_code=400)
    return summary
```

- [ ] **Step 3d: Register routers**

In `solaranalysis/web/app.py`, add after the plants/settings includes:

```python
    from .routes.schedules import router as schedules_router
    from .routes.imports import router as imports_router
    app.include_router(schedules_router, prefix="/api/schedules")
    app.include_router(imports_router, prefix="/api/import")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/web/test_importer.py tests/web/test_api_schedules.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add tests/web/test_importer.py tests/web/test_api_schedules.py solaranalysis/web/importer.py solaranalysis/web/routes/schedules.py solaranalysis/web/routes/imports.py solaranalysis/web/app.py
git commit -m "feat(web): schedules routes and one-time import"
```

---

### Task 21: Runs routes (list, create, get, cancel, log)

**Files:**
- Create: `solaranalysis/web/routes/runs.py`
- Modify: `solaranalysis/web/app.py`
- Test: `tests/web/test_api_runs.py`

**Interfaces:**
- Produces runs router:
  - `GET /api/runs?limit=&offset=` → `repo.list_runs`.
  - `POST /api/runs {time_range}` → `run_manager.start_run("manual", time_range)`; 201 `{id}`; 409 `{active}` on `Busy`; 422 on bad time_range.
  - `GET /api/runs/{id}` → `repo.get_run` merged with `run_manager.get_progress` under key `"progress"` when the run is live.
  - `POST /api/runs/{id}/cancel` → `run_manager.cancel`; 200 `{cancelled:bool}`.
  - `GET /api/runs/{id}/log` → `{"log": <full file text>}` (empty string if the file is absent), read from `paths.data_dir / log_path`.
- Consumes: `run_manager` (15,16), `repo` (8), `paths`.

- [ ] **Step 1: Write the failing test**

`tests/web/test_api_runs.py`:

```python
import hashlib, os
from fastapi.testclient import TestClient
from solaranalysis.web import db, repo
from solaranalysis.web.app import create_app
from solaranalysis.web.paths import Paths
from solaranalysis.web.run_manager import Busy

CSRF = {"X-Solar-CSRF": "1"}


class FakeRM:
    def __init__(self, busy=False):
        self.busy = busy
        self.cancelled = None
        self._progress = {"plants": {"A": "running"}, "status": "running"}
    def start_run(self, trigger, time_range):
        if self.busy:
            raise Busy({"kind": "run", "id": 1})
        return 5
    def get_progress(self, rid): return self._progress
    def cancel(self, rid):
        self.cancelled = rid; return True


def _client(tmp_path, rm):
    app_dir = tmp_path / "app"; app_dir.mkdir()
    paths = Paths.create(str(tmp_path / "data"), str(app_dir))
    conn = db.connect(paths.db_path); db.init_db(conn)
    repo.set_setup_token_hash(conn, hashlib.sha256(b"t").hexdigest())
    conn.close()
    app = create_app(paths, run_manager=rm)
    client = TestClient(app)
    client.post("/api/auth/setup", json={"token": "t", "password": "pw"}, headers=CSRF)
    return client, paths


def test_create_run_ok(tmp_path):
    client, _ = _client(tmp_path, FakeRM())
    r = client.post("/api/runs", headers=CSRF, json={"time_range": "30d"})
    assert r.status_code == 201 and r.json()["id"] == 5


def test_create_run_bad_range(tmp_path):
    client, _ = _client(tmp_path, FakeRM())
    r = client.post("/api/runs", headers=CSRF, json={"time_range": "bogus"})
    assert r.status_code == 422


def test_create_run_busy(tmp_path):
    client, _ = _client(tmp_path, FakeRM(busy=True))
    r = client.post("/api/runs", headers=CSRF, json={"time_range": "30d"})
    assert r.status_code == 409 and r.json()["active"]["kind"] == "run"


def test_get_run_merges_progress(tmp_path):
    rm = FakeRM()
    client, paths = _client(tmp_path, rm)
    conn = db.connect(paths.db_path)
    rid = repo.create_run(conn, trigger="manual", time_range="30d",
                          log_path="logs/run-1.log", started_at="2026-07-04T00:00:00")
    conn.close()
    r = client.get(f"/api/runs/{rid}").json()
    assert r["status"] == "running"
    assert r["progress"]["plants"]["A"] == "running"


def test_cancel(tmp_path):
    rm = FakeRM()
    client, _ = _client(tmp_path, rm)
    r = client.post("/api/runs/5/cancel", headers=CSRF)
    assert r.status_code == 200 and r.json()["cancelled"] is True
    assert rm.cancelled == 5


def test_log_reads_file(tmp_path):
    rm = FakeRM()
    client, paths = _client(tmp_path, rm)
    conn = db.connect(paths.db_path)
    rid = repo.create_run(conn, trigger="manual", time_range="30d",
                          log_path="logs/run-9.log", started_at="2026-07-04T00:00:00")
    conn.close()
    os.makedirs(os.path.join(paths.data_dir, "logs"), exist_ok=True)
    with open(os.path.join(paths.data_dir, "logs", "run-9.log"), "w", encoding="utf-8") as f:
        f.write("line one\nline two\n")
    r = client.get(f"/api/runs/{rid}/log").json()
    assert "line two" in r["log"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/web/test_api_runs.py -v`
Expected: FAIL (router not registered).

- [ ] **Step 3: Implement the runs router**

`solaranalysis/web/routes/runs.py`:

```python
from __future__ import annotations
import os
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import repo
from ..run_manager import Busy

router = APIRouter()
_RANGES = {"snapshot", "30d", "12mo", "all"}


class RunBody(BaseModel):
    time_range: str


def _conn(request: Request):
    yield from request.app.state.db_dep()


@router.get("")
def list_runs(limit: int = 50, offset: int = 0, conn=Depends(_conn)):
    return repo.list_runs(conn, limit=limit, offset=offset)


@router.post("")
def create_run(body: RunBody, request: Request):
    if body.time_range not in _RANGES:
        return JSONResponse({"detail": "invalid time_range"}, status_code=422)
    rm = request.app.state.run_manager
    try:
        rid = rm.start_run("manual", body.time_range)
    except Busy as b:
        return JSONResponse({"detail": "busy", "active": b.active}, status_code=409)
    return JSONResponse({"id": rid}, status_code=201)


@router.get("/{rid}")
def get_run(rid: int, request: Request, conn=Depends(_conn)):
    run = repo.get_run(conn, rid)
    if not run:
        return JSONResponse({"detail": "not found"}, status_code=404)
    rm = request.app.state.run_manager
    if run["status"] == "running" and rm:
        prog = rm.get_progress(rid)
        if prog:
            run["progress"] = prog
    return run


@router.post("/{rid}/cancel")
def cancel_run(rid: int, request: Request):
    rm = request.app.state.run_manager
    return {"cancelled": bool(rm and rm.cancel(rid))}


@router.get("/{rid}/log")
def run_log(rid: int, request: Request, conn=Depends(_conn)):
    run = repo.get_run(conn, rid)
    if not run:
        return JSONResponse({"detail": "not found"}, status_code=404)
    path = os.path.join(request.app.state.paths.data_dir, run["log_path"])
    text = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    return {"log": text}
```

Register in `app.py` after the import router include:

```python
    from .routes.runs import router as runs_router
    app.include_router(runs_router, prefix="/api/runs")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/web/test_api_runs.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add tests/web/test_api_runs.py solaranalysis/web/routes/runs.py solaranalysis/web/app.py
git commit -m "feat(web): runs routes (list/create/get/cancel/log)"
```

---

### Task 22: SSE stream and hardened report serving

**Files:**
- Modify: `solaranalysis/web/routes/runs.py`
- Modify: `solaranalysis/web/app.py` (nothing new to register — same router)
- Test: `tests/web/test_api_stream_report.py`

**Interfaces:**
- Produces (on the runs router):
  - `GET /api/runs/{id}/stream` → `text/event-stream`. Subscribes to `run_manager`, yields `data: <json>\n\n` per message; stops after a `{"type":"end"}` message or when the run is not running and no manager is present. Always `unsubscribe`s in a `finally`.
  - `GET /api/runs/{id}/report` → serves the report file with `Content-Security-Policy: sandbox; default-src 'none'`, `X-Content-Type-Options: nosniff`, `media_type=text/html`. Resolves `report_path` against `paths.data_dir`, rejects (404) if the real path escapes `paths.output_dir` or is missing.
- Consumes: `run_manager.subscribe/unsubscribe` (15), `repo`, `paths`.

- [ ] **Step 1: Write the failing test**

`tests/web/test_api_stream_report.py`:

```python
import hashlib, os, queue, json
from fastapi.testclient import TestClient
from solaranalysis.web import db, repo
from solaranalysis.web.app import create_app
from solaranalysis.web.paths import Paths

CSRF = {"X-Solar-CSRF": "1"}


class FakeRM:
    def __init__(self):
        self.q = queue.Queue()
        self.unsub = 0
    def subscribe(self, rid):
        self.q.put({"type": "log", "line": "hello"})
        self.q.put({"type": "end"})
        return self.q
    def unsubscribe(self, rid, q): self.unsub += 1
    def get_progress(self, rid): return None


def _client(tmp_path, rm):
    app_dir = tmp_path / "app"; app_dir.mkdir()
    paths = Paths.create(str(tmp_path / "data"), str(app_dir))
    conn = db.connect(paths.db_path); db.init_db(conn)
    repo.set_setup_token_hash(conn, hashlib.sha256(b"t").hexdigest())
    conn.close()
    app = create_app(paths, run_manager=rm)
    client = TestClient(app)
    client.post("/api/auth/setup", json={"token": "t", "password": "pw"}, headers=CSRF)
    return client, paths


def _make_run(paths, report_rel):
    conn = db.connect(paths.db_path)
    rid = repo.create_run(conn, trigger="manual", time_range="30d",
                          log_path="logs/x.log", started_at="2026-07-04T00:00:00")
    repo.finalize_run(conn, rid, status="success", finished_at="2026-07-04T00:01:00",
                      report_path=report_rel, plants_summary=[], skipped_plants=[],
                      notes={}, error=None)
    conn.close()
    return rid


def test_stream_yields_until_end(tmp_path):
    rm = FakeRM()
    client, paths = _client(tmp_path, rm)
    rid = _make_run(paths, None)
    with client.stream("GET", f"/api/runs/{rid}/stream") as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        body = "".join(chunk for chunk in r.iter_text())
    assert "hello" in body
    assert rm.unsub == 1


def test_report_served_with_csp(tmp_path):
    rm = FakeRM()
    client, paths = _client(tmp_path, rm)
    rel = "output/20260704-000000/report.html"
    full = os.path.join(paths.data_dir, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write("<html><body>Report</body></html>")
    rid = _make_run(paths, rel)
    r = client.get(f"/api/runs/{rid}/report")
    assert r.status_code == 200
    assert "sandbox" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "Report" in r.text


def test_report_path_traversal_rejected(tmp_path):
    rm = FakeRM()
    client, paths = _client(tmp_path, rm)
    # Craft a report_path that escapes output/.
    outside = os.path.join(paths.data_dir, "secret.txt")
    with open(outside, "w", encoding="utf-8") as f:
        f.write("TOPSECRET")
    rid = _make_run(paths, "output/../secret.txt")
    r = client.get(f"/api/runs/{rid}/report")
    assert r.status_code == 404


def test_report_missing_file_404(tmp_path):
    rm = FakeRM()
    client, paths = _client(tmp_path, rm)
    rid = _make_run(paths, "output/nope/report.html")
    assert client.get(f"/api/runs/{rid}/report").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/web/test_api_stream_report.py -v`
Expected: FAIL (endpoints not defined).

- [ ] **Step 3: Implement**

Append to `solaranalysis/web/routes/runs.py`:

```python
import json as _json
import queue as _queue
from fastapi.responses import StreamingResponse, Response


@router.get("/{rid}/stream")
def stream_run(rid: int, request: Request):
    rm = request.app.state.run_manager

    def gen():
        if not rm:
            return
        q = rm.subscribe(rid)
        try:
            while True:
                try:
                    msg = q.get(timeout=30)
                except _queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {_json.dumps(msg)}\n\n"
                if msg.get("type") == "end":
                    break
        finally:
            rm.unsubscribe(rid, q)

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/{rid}/report")
def run_report(rid: int, request: Request, conn=Depends(_conn)):
    run = repo.get_run(conn, rid)
    if not run or not run["report_path"]:
        return JSONResponse({"detail": "not found"}, status_code=404)
    paths = request.app.state.paths
    full = os.path.realpath(os.path.join(paths.data_dir, run["report_path"]))
    out_root = os.path.realpath(paths.output_dir)
    if not full.startswith(out_root + os.sep) or not os.path.isfile(full):
        return JSONResponse({"detail": "not found"}, status_code=404)
    with open(full, encoding="utf-8", errors="replace") as f:
        html = f.read()
    return Response(content=html, media_type="text/html", headers={
        "Content-Security-Policy": "sandbox; default-src 'none'",
        "X-Content-Type-Options": "nosniff",
    })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/web/test_api_stream_report.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add tests/web/test_api_stream_report.py solaranalysis/web/routes/runs.py
git commit -m "feat(web): SSE stream and XSS-hardened report serving"
```

---

### Task 23: Static SPA serving and entry point

**Files:**
- Modify: `solaranalysis/web/app.py`
- Create: `solaranalysis/web/__main__.py`
- Test: `tests/web/test_spa_serving.py`

**Interfaces:**
- Produces:
  - In `create_app`: after all API routers, mount a catch-all that serves `frontend/dist/index.html` for any non-`/api` path (SPA client routing), and serves built assets under `/assets`. When `frontend/dist` is absent (dev/test), the catch-all returns a small placeholder page so the server still boots.
  - `__main__.py`: `main(argv=None)` — argparse `--host` (default `0.0.0.0`), `--port` (8000), `--data-dir` (`./data`), `--app-dir` (default: the project root two levels up from this file). Wires `RunManager` + `ScheduleService`, builds the app, runs uvicorn.
- Consumes: everything above.

- [ ] **Step 1: Write the failing test**

`tests/web/test_spa_serving.py`:

```python
import hashlib
from fastapi.testclient import TestClient
from solaranalysis.web import db, repo
from solaranalysis.web.app import create_app
from solaranalysis.web.paths import Paths


def _client(tmp_path):
    app_dir = tmp_path / "app"; app_dir.mkdir()
    paths = Paths.create(str(tmp_path / "data"), str(app_dir))
    conn = db.connect(paths.db_path); db.init_db(conn); conn.close()
    return TestClient(create_app(paths))


def test_root_serves_spa_placeholder(tmp_path):
    client = _client(tmp_path)
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_client_route_falls_through_to_spa(tmp_path):
    client = _client(tmp_path)
    # An unknown non-API path must return the SPA shell, not 404.
    r = client.get("/plants")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_unknown_api_route_is_404(tmp_path):
    client = _client(tmp_path)
    assert client.get("/api/does-not-exist").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/web/test_spa_serving.py -v`
Expected: FAIL (catch-all not implemented — `/plants` 404s).

- [ ] **Step 3a: Implement SPA serving**

In `solaranalysis/web/app.py`, add near the top:

```python
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
```

At the end of `create_app`, before `return app`, add:

```python
    dist = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                        "frontend", "dist")
    assets = os.path.join(dist, "assets")
    if os.path.isdir(assets):
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    _PLACEHOLDER = ("<!doctype html><meta charset='utf-8'>"
                    "<title>Solar Analysis</title>"
                    "<p>Frontend not built. Run <code>npm run build</code> in "
                    "<code>frontend/</code>.</p>")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "not found"}, status_code=404)
        index = os.path.join(dist, "index.html")
        if os.path.isfile(index):
            return FileResponse(index)
        return HTMLResponse(_PLACEHOLDER)
```

(The `/{full_path:path}` catch-all is registered last, so it only matches paths no API router claimed.)

- [ ] **Step 3b: Implement the entry point**

`solaranalysis/web/__main__.py`:

```python
from __future__ import annotations
import argparse
import os

from .paths import Paths
from .app import create_app
from .run_manager import RunManager
from .scheduler import ScheduleService


def build(data_dir: str, app_dir: str):
    paths = Paths.create(data_dir, app_dir)
    rm = RunManager(paths)
    sched = ScheduleService(paths, rm)
    app = create_app(paths, run_manager=rm, schedule_service=sched)
    return app, paths


def main(argv=None) -> int:
    default_app_dir = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    ap = argparse.ArgumentParser(prog="solaranalysis.web")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--data-dir", default="./data")
    ap.add_argument("--app-dir", default=default_app_dir)
    args = ap.parse_args(argv)
    app, _ = build(args.data_dir, args.app_dir)
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/web/test_spa_serving.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the whole backend suite**

Run: `python -m pytest -q`
Expected: PASS (all existing + all new web tests).

- [ ] **Step 6: Commit**

```bash
git add tests/web/test_spa_serving.py solaranalysis/web/app.py solaranalysis/web/__main__.py
git commit -m "feat(web): SPA serving and server entry point"
```

---

## Phase 7 — Frontend (React + TypeScript)

**Design note:** The spec defers visual design to the frontend-design workflow. Tasks 25–27 therefore specify each view's **data contract, states, and interactions** in full, but hand the JSX/styling to `frontend-design` at execution time. The infrastructure (build config, typed API client, SSE hook, auth gate, routing shell) is written in full below. **Verification for every frontend task is `npm run build` passing** (Vite + `tsc --noEmit` via `tsc -b`), plus the end-to-end smoke in Task 28.

Run all frontend commands from `frontend/`.

### Task 24: Scaffold, typed API client, SSE hook, auth gate, routing shell

**Files:**
- Create: `frontend/package.json`, `frontend/tsconfig.json`, `frontend/vite.config.ts`, `frontend/index.html`
- Create: `frontend/src/main.tsx`, `frontend/src/api.ts`, `frontend/src/sse.ts`, `frontend/src/auth.tsx`, `frontend/src/App.tsx`
- Create: `frontend/.gitignore`

**Interfaces:**
- Produces: a buildable SPA whose `api.ts` exposes typed functions for every endpoint (auth/plants/settings/schedules/runs/import), always sending the `X-Solar-CSRF: 1` header on mutations and treating 401 as "logged out"; `useRunStream(runId)` SSE hook; `<AuthGate>` that renders login/setup vs. the app based on `GET /api/auth/status`; `<App>` with React Router routes for `/`, `/plants`, `/runs`, `/runs/:id`, `/schedules`, `/settings`.

- [ ] **Step 1: Create build config**

`frontend/package.json`:

```json
{
  "name": "solar-web",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "@tanstack/react-query": "^5.51.0",
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.24.0"
  },
  "devDependencies": {
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "typescript": "^5.5.3",
    "vite": "^5.3.3"
  }
}
```

`frontend/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true
  },
  "include": ["src"]
}
```

`frontend/vite.config.ts`:

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000" } },
  build: { outDir: "dist" },
});
```

`frontend/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Solar Analysis</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`frontend/.gitignore`:

```
node_modules/
dist/
```

- [ ] **Step 2: Install**

Run: `cd frontend && npm install`
Expected: dependencies install without error.

- [ ] **Step 3: Implement the typed API client**

`frontend/src/api.ts`:

```ts
export class AuthError extends Error {}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  const opts: RequestInit = { method, headers, credentials: "same-origin" };
  if (method !== "GET") headers["X-Solar-CSRF"] = "1";
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  if (res.status === 401) throw new AuthError("unauthorized");
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export interface Plant {
  id: number; name: string; platform: "solaredge" | "growatt" | "sma";
  auth_mode: "password" | "token"; username: string | null;
  has_password: boolean; has_token: boolean;
  tariff_per_kwh: number | null; currency: string | null; enabled: boolean;
  last_test_at: string | null; last_test_ok: boolean | null; last_test_error: string | null;
}
export interface Settings { model: string | null; max_input_tokens: number; output_language: string; }
export interface Schedule {
  id: number; time_of_day: string; days_of_week: string;
  time_range: TimeRange; enabled: boolean;
}
export type TimeRange = "snapshot" | "30d" | "12mo" | "all";
export type RunStatus = "running" | "success" | "partial" | "failed" | "cancelled" | "interrupted";
export interface Run {
  id: number; status: RunStatus; trigger: "manual" | "scheduled";
  time_range: TimeRange; started_at: string; finished_at: string | null;
  report_path: string | null; log_path: string;
  plants_summary: { name: string; ok: boolean; reason?: string }[] | null;
  skipped_plants: { name: string; reason: string }[] | null;
  notes: Record<string, unknown> | null; error: string | null;
  progress?: { plants: Record<string, string>; status: string };
}

export const api = {
  status: () => req<{ setup_required: boolean; authenticated: boolean }>("GET", "/api/auth/status"),
  setup: (token: string, password: string) => req("POST", "/api/auth/setup", { token, password }),
  login: (password: string) => req("POST", "/api/auth/login", { password }),
  logout: () => req("POST", "/api/auth/logout"),
  changePassword: (oldPw: string, newPw: string) => req("PUT", "/api/auth/password", { old: oldPw, new: newPw }),

  plants: () => req<Plant[]>("GET", "/api/plants"),
  createPlant: (data: Partial<Plant> & { password?: string; token?: string }) =>
    req<{ id: number }>("POST", "/api/plants", data),
  updatePlant: (id: number, data: Partial<Plant> & { password?: string; token?: string }) =>
    req("PUT", `/api/plants/${id}`, data),
  deletePlant: (id: number) => req("DELETE", `/api/plants/${id}`),
  testPlant: (id: number) => req<{ ok: boolean; error: string | null }>("POST", `/api/plants/${id}/test`),

  settings: () => req<Settings>("GET", "/api/settings"),
  saveSettings: (s: Settings) => req("PUT", "/api/settings", s),

  schedules: () => req<Schedule[]>("GET", "/api/schedules"),
  createSchedule: (s: Omit<Schedule, "id">) => req<{ id: number }>("POST", "/api/schedules", s),
  updateSchedule: (id: number, s: Partial<Schedule>) => req("PUT", `/api/schedules/${id}`, s),
  deleteSchedule: (id: number) => req("DELETE", `/api/schedules/${id}`),

  runs: () => req<Run[]>("GET", "/api/runs"),
  run: (id: number) => req<Run>("GET", `/api/runs/${id}`),
  startRun: (time_range: TimeRange) => req<{ id: number }>("POST", "/api/runs", { time_range }),
  cancelRun: (id: number) => req<{ cancelled: boolean }>("POST", `/api/runs/${id}/cancel`),
  runLog: (id: number) => req<{ log: string }>("GET", `/api/runs/${id}/log`),
  reportUrl: (id: number) => `/api/runs/${id}/report`,

  runImport: () => req<Record<string, unknown>>("POST", "/api/import"),
};
```

- [ ] **Step 4: Implement the SSE hook**

`frontend/src/sse.ts`:

```ts
import { useEffect, useState } from "react";

export interface StreamMsg {
  type: "log" | "progress" | "end";
  line?: string;
  event?: Record<string, unknown>;
}

export function useRunStream(runId: number | null, active: boolean) {
  const [logLines, setLogLines] = useState<string[]>([]);
  const [lastEvent, setLastEvent] = useState<Record<string, unknown> | null>(null);
  const [ended, setEnded] = useState(false);

  useEffect(() => {
    if (runId == null || !active) return;
    setLogLines([]); setEnded(false);
    const es = new EventSource(`/api/runs/${runId}/stream`, { withCredentials: true });
    es.onmessage = (e) => {
      const msg: StreamMsg = JSON.parse(e.data);
      if (msg.type === "log" && msg.line) setLogLines((p) => [...p, msg.line!]);
      else if (msg.type === "progress") setLastEvent(msg.event ?? null);
      else if (msg.type === "end") { setEnded(true); es.close(); }
    };
    es.onerror = () => { es.close(); };  // caller re-fetches on reconnect
    return () => es.close();
  }, [runId, active]);

  return { logLines, lastEvent, ended };
}
```

- [ ] **Step 5: Implement the auth gate and app shell**

`frontend/src/auth.tsx`:

```tsx
import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { api } from "./api";

interface AuthState { authenticated: boolean; setupRequired: boolean; refresh: () => Promise<void>; }
const Ctx = createContext<AuthState>(null as unknown as AuthState);
export const useAuth = () => useContext(Ctx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [authenticated, setAuth] = useState(false);
  const [setupRequired, setSetup] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const refresh = async () => {
    const s = await api.status();
    setAuth(s.authenticated); setSetup(s.setup_required); setLoaded(true);
  };
  useEffect(() => { void refresh(); }, []);
  if (!loaded) return <p>Loading…</p>;
  return <Ctx.Provider value={{ authenticated, setupRequired, refresh }}>{children}</Ctx.Provider>;
}
```

`frontend/src/App.tsx`:

```tsx
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "./auth";

const qc = new QueryClient();

function Shell() {
  const { authenticated, setupRequired } = useAuth();
  if (!authenticated) {
    // LoginOrSetup is built in a later task; placeholder keeps the build green.
    return <p>{setupRequired ? "Setup required" : "Please log in"}</p>;
  }
  return (
    <Routes>
      <Route path="/" element={<div>Dashboard</div>} />
      <Route path="/plants" element={<div>Plants</div>} />
      <Route path="/runs" element={<div>Runs</div>} />
      <Route path="/runs/:id" element={<div>Run detail</div>} />
      <Route path="/schedules" element={<div>Schedules</div>} />
      <Route path="/settings" element={<div>Settings</div>} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <AuthProvider>
        <BrowserRouter><Shell /></BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
}
```

`frontend/src/main.tsx`:

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><App /></React.StrictMode>
);
```

- [ ] **Step 6: Verify the build passes**

Run: `cd frontend && npm run build`
Expected: `tsc -b` and `vite build` both succeed; `dist/index.html` produced.

- [ ] **Step 7: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/tsconfig.json frontend/vite.config.ts frontend/index.html frontend/.gitignore frontend/src
git commit -m "feat(frontend): scaffold, typed API client, SSE hook, auth gate"
```

---

### Task 25: Login/Setup screen, Plants view, Settings view

**Files:**
- Create: `frontend/src/routes/LoginOrSetup.tsx`, `frontend/src/routes/Plants.tsx`, `frontend/src/routes/Settings.tsx`, `frontend/src/nav.tsx` (shared nav shell)
- Modify: `frontend/src/App.tsx` (wire the real components)

**Design handoff:** Invoke `frontend-design` to build these three screens + the nav shell. Consume the exact `api.ts` types/functions. Do not invent endpoints.

**Contracts & behavior (must be implemented exactly):**

- **LoginOrSetup** — reads `useAuth()`. If `setupRequired`: a form with a **setup token** field (the operator reads it from the server console) + **new password**; submit → `api.setup(token, password)` → `refresh()`. Else: a **password** field; submit → `api.login(password)` → `refresh()`; on `429` show "too many attempts, wait a minute"; on error show the message.
- **Plants** — `useQuery(["plants"], api.plants)`. Table columns: name, platform badge, auth mode, enabled toggle (`api.updatePlant(id,{enabled})`), last test status (ok/failed/never + timestamp), actions: Edit, Test, Delete (confirm).
  - **Test** button → `api.testPlant(id)`, show spinner while pending, then a ✓/✗ with the returned error; on `409` show "busy — a run or test is in progress".
  - **Add/Edit form** fields: name, platform select, auth mode select (**token option shown only when platform is growatt**), username, password (placeholder "leave blank to keep current" in edit mode), token (growatt+token only, same placeholder), tariff_per_kwh, currency, enabled. Submit → `api.createPlant`/`api.updatePlant`. Mirror server validation client-side (§11) so bad input is caught before submit; still surface server `422` detail.
- **Settings** — `useQuery(["settings"], api.settings)`. Fields: model (text, empty = auto), max_input_tokens (number), output_language (select en/he). Save → `api.saveSettings`. Separate "Change password" sub-form → `api.changePassword(old,new)` (on success, user is logged out by epoch bump — call `refresh()`; show "password changed, please log in again"). **Import** button visible always but calling `api.runImport()`; show the returned summary (created/updated plant names, which secrets resolved) or the error; on `404` show "no config.yaml found on the server".
- **nav.tsx** — persistent nav linking the five routes + a Logout button (`api.logout()` → `refresh()`).

- [ ] **Step 1:** Invoke `frontend-design` with the contracts above; build the four files and wire them into `App.tsx` (replace the placeholder route elements and the `!authenticated` branch with `<LoginOrSetup/>`).
- [ ] **Step 2: Verify build**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): login/setup, plants, settings views"
```

---

### Task 26: Runs view (history + detail with live log and report)

**Files:**
- Create: `frontend/src/routes/Runs.tsx`, `frontend/src/routes/RunDetail.tsx`
- Modify: `frontend/src/App.tsx`

**Design handoff:** Invoke `frontend-design`. Consume `api.ts` + `useRunStream`.

**Contracts & behavior:**

- **Runs** (`/runs`) — `useQuery(["runs"], api.runs)`. Table: id, status chip (color per `RunStatus`), trigger, time_range, started_at, duration (finished−started, or "—" while running), link to `/runs/:id`. A "Run now" control with a `TimeRange` select → `api.startRun(range)` → navigate to the new run's detail; on `409` show "a run/test is already active".
- **RunDetail** (`/runs/:id`) — `useQuery(["run", id], () => api.run(id))`, refetch while `status === "running"`.
  - **Per-plant step indicators:** while running, drive from `run.progress.plants` (map name→state chip) updated via `useRunStream(id, running)`'s `lastEvent`; for completed runs, render from `run.plants_summary`.
  - **Live log:** while running, `useRunStream` `logLines` in an auto-scrolling `<pre>`; on SSE error/reconnect, re-fetch `api.run(id)` and `api.runLog(id)` to resync. For completed runs, show `api.runLog(id)` once.
  - **Cancel** button while running → `api.cancelRun(id)`.
  - **Report:** when `report_path` is set, embed `api.reportUrl(id)` in `<iframe sandbox="allow-same-origin">` (no `allow-scripts`) + an "Open report" link (`target="_blank"`) to the same URL.
  - Show `notes.verify_missing_count` (if > 0) as a subtle note, and the skipped-plants list with reasons.

- [ ] **Step 1:** Invoke `frontend-design`; build both files; wire routes in `App.tsx`.
- [ ] **Step 2: Verify build**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): runs history and live run detail"
```

---

### Task 27: Dashboard and Schedules views

**Files:**
- Create: `frontend/src/routes/Dashboard.tsx`, `frontend/src/routes/Schedules.tsx`
- Modify: `frontend/src/App.tsx`

**Design handoff:** Invoke `frontend-design`. Consume `api.ts`.

**Contracts & behavior:**

- **Dashboard** (`/`) — shows: the active operation (poll `api.runs()` / most recent `running` run, link to its detail) or the last completed run summary; a "Run now" control (`TimeRange` select → `api.startRun`); the next scheduled run (compute the soonest from `api.schedules()` enabled rows, client-side); per-plant health chips from `api.plants()` (enabled + `last_test_ok`).
- **Schedules** (`/schedules`) — `useQuery(["schedules"], api.schedules)`. List each schedule with an enabled toggle (`api.updateSchedule`), time, weekday summary, time range, delete. Editor form: time (`HH:MM`), weekday checkboxes (Mon=0…Sun=6 → CSV `days_of_week`), `TimeRange` select → `api.createSchedule`/`api.updateSchedule`.

- [ ] **Step 1:** Invoke `frontend-design`; build both files; wire routes in `App.tsx`.
- [ ] **Step 2: Verify build**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): dashboard and schedules views"
```

---

### Task 28: End-to-end smoke, README, deployment notes

**Files:**
- Modify: `README.md`
- Create: `docs/superpowers/plans/notes-web-ui-deploy.md` (NSSM service instructions)

- [ ] **Step 1: Full backend suite**

Run: `python -m pytest -q`
Expected: all pass.

- [ ] **Step 2: Build the frontend**

Run: `cd frontend && npm run build`
Expected: `frontend/dist` produced.

- [ ] **Step 3: Manual end-to-end smoke**

Start the server against a scratch data dir:

Run: `python -m solaranalysis.web --data-dir ./data-smoke --port 8000`

Verify, capturing evidence:
1. Server log prints a `SETUP TOKEN: ...`.
2. Open `http://localhost:8000/` → setup screen; enter the token + a password → lands in the app.
3. Settings → Import (if a `config.yaml` exists) OR Plants → add a plant → verify it appears with `has_password` and no secret leaks (check `GET /api/plants` in devtools shows no password).
4. Plants → Test on a plant → observe ✓/✗ (a real portal login attempt).
5. Dashboard → Run now (`30d`) → Run detail shows live per-plant steps + streaming log; on completion the report renders in the iframe.
6. Confirm a second "Run now" while one is active returns the "busy" message.
7. Change password in Settings → confirm you're logged out and must re-log.

- [ ] **Step 4: Document in README**

Add a "Web UI" section to `README.md` covering: install (`pip install -r requirements.txt`, `cd frontend && npm install && npm run build`), run (`python -m solaranalysis.web`), the setup-token first-boot flow, the credential threat model (Fernet at rest; `secret.key` + `app.db` together are as sensitive as the old `.env`), and that the CLI still works file-based.

- [ ] **Step 5: Write NSSM deployment note**

`docs/superpowers/plans/notes-web-ui-deploy.md`: how to run the server as a Windows service via NSSM (application = the Python interpreter from Global Constraints; arguments = `-m solaranalysis.web --data-dir <abs> --app-dir <abs>`; working dir; where the setup token appears in the service stdout log). Reference the `nssm-service-manager` skill.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/superpowers/plans/notes-web-ui-deploy.md
git commit -m "docs(web): README web UI section and NSSM deploy notes"
```

---

## Self-Review

**1. Spec coverage** (each spec section → task):

- §2 Architecture / path resolution → Tasks 2, 14, 15, 23 (Paths, runner args, subprocess spawn, `--data-dir`/`--app-dir`).
- §3 Data model (all columns incl. `session_epoch`, `setup_token`, `runner_pid`, `plants_summary`, `notes`, time_range CHECK) → Tasks 5, 6, 8.
- §4 Credential security (Fernet, ACL, `has_*`, stream-wide redaction, PBKDF2) → Tasks 3, 4, 7, 13, 14, 15.
- §5 Auth (setup token, epoch sessions, logout, CSRF, rate limit, authz boundary) → Tasks 4, 18.
- §6 Run execution (single lock across runs+tests, lifecycle, cancel/exit ordering, event protocol, per-plant session key, verify_login, progress) → Tasks 9, 10, 11, 14, 15, 16, 21.
- §7 Scheduling → Task 17 (+ reload wired in Task 20).
- §8 API surface → Tasks 18–23 (every listed endpoint).
- §9 Frontend views → Tasks 24–27.
- §10 Report serving (CSP, traversal) + error handling (interrupted recovery, SSE resync) → Tasks 16, 22, 26.
- §11 Validation → Task 19 (`validate_plant`) + repo update rules Task 7.
- §12 Import → Task 20.
- §13 Testing → tests accompany every backend task; frontend build gate Tasks 24–27; e2e Task 28.
- §14 Dependencies → Task 1.
- §15 Deployment → Tasks 23, 28.

No spec section is unimplemented. (`itsdangerous` from spec §14 is intentionally dropped in favor of stdlib HMAC signing — noted in Global Constraints/Task 4.)

**2. Placeholder scan:** No "TBD"/"implement later". Frontend view tasks delegate JSX to `frontend-design` but pin exact contracts, endpoints, and behavior — an implementer has everything needed. The `App.tsx` placeholders in Task 24 are real, buildable code replaced in Tasks 25–27.

**3. Type consistency:** `Paths`, `AuthConfig` (existing), `plant_public`/`run_public` shapes, `RunManager.Busy.active` (`{kind,id}`), event names, `EVENT_PREFIX`, `X-Solar-CSRF`, cookie `solar_session`, and the `api.ts` types all match across tasks. `run_test`/`start_run`/`cancel`/`get_progress`/`subscribe`/`unsubscribe`/`reconcile_on_startup` are named identically where produced and consumed.

---

## Execution note (interfaces the tests lean on)

Two production methods exist primarily to make deterministic tests possible; they are part of the contract, not test-only hacks:
- `RunManager.join(run_id, timeout)` — waits for a run's pump thread (used by tests to avoid sleeps; harmless in production).
- `RunManager(spawn=...)` and `ScheduleService(scheduler=...)` — dependency injection points for the subprocess spawner and APScheduler instance.

