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


# Note: a true mid-stream client disconnect can't be simulated under Starlette's
# TestClient, which buffers the full response — the async rewrite (polling via
# get_nowait + is_disconnected) is what fixes the disconnect leak in production.
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


def test_stream_404_for_missing_run(tmp_path):
    rm = FakeRM()
    client, paths = _client(tmp_path, rm)
    r = client.get("/api/runs/999/stream")
    assert r.status_code == 404


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
    csp = r.headers["content-security-policy"]
    assert "sandbox" in csp
    assert "default-src 'none'" in csp
    assert "style-src 'unsafe-inline'" in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "Report" in r.text


def test_report_with_inline_style_is_served(tmp_path):
    rm = FakeRM()
    client, paths = _client(tmp_path, rm)
    rel = "output/20260704-000001/report.html"
    full = os.path.join(paths.data_dir, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write("<html><head><style>body{color:red}</style></head>"
                 "<body>Styled</body></html>")
    rid = _make_run(paths, rel)
    r = client.get(f"/api/runs/{rid}/report")
    assert r.status_code == 200
    assert "<style>body{color:red}</style>" in r.text
    csp = r.headers["content-security-policy"]
    assert "style-src 'unsafe-inline'" in csp


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
