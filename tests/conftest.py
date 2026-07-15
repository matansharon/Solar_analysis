import pytest

# Every Graph credential the mailer reads. Scrubbed from the environment for
# every test so the suite can never send real email — this covers both the
# in-process mailer (is_configured()/recipients() read os.getenv) AND any real
# runner subprocess a test spawns, which inherits this process's environment.
# Without this, a developer whose shell has real GRAPH_* set would have the
# runner tests that don't stub the mailer email fixture content to the real
# recipient.
_GRAPH_ENV_KEYS = (
    "GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET",
    "GRAPH_SENDER", "REPORT_RECIPIENTS",
)


@pytest.fixture(autouse=True)
def _scrub_graph_env(monkeypatch):
    for key in _GRAPH_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
