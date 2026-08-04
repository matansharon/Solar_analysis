import pytest

# Two groups, both scrubbed for every test.
#
# The Graph credentials the mailer reads: scrubbed so the suite can never send
# real email — this covers both the in-process mailer (is_configured()/
# recipients() read os.getenv) AND any real runner subprocess a test spawns,
# which inherits this process's environment. Without this, a developer whose
# shell has real GRAPH_* set would have the runner tests that don't stub the
# mailer email fixture content to the real recipient.
#
# SOLAR_NO_EMAIL is the opposite hazard: an inherited or leaked suppression flag
# silently turns the runner's email path off, so tests that assert a report was
# emailed fail somewhere far from the cause. Task 8's orchestrator test writes
# it into os.environ through main(), so without this every later test in the
# session — all of tests/web/test_runner.py — would run suppressed.
_SCRUBBED_ENV_KEYS = (
    "GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET",
    "GRAPH_SENDER", "REPORT_RECIPIENTS", "PIPELINE_RECIPIENTS",
    "SOLAR_NO_EMAIL",
)


@pytest.fixture(autouse=True)
def _scrub_graph_env(monkeypatch):
    for key in _SCRUBBED_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
