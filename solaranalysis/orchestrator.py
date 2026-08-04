"""One scheduled entry point for the whole pipeline.

    python -m solaranalysis.orchestrator --data-dir DIR --app-dir DIR

Runs three stages in order, each in its own process so one stage cannot take
the others down: the fleet comparison run (through the web app's RunManager, so
it still shows up in run history), then the SolarEdge optimizer collector, then
the Growatt string collector. Continue-on-failure; the exit code is a bitmask
naming which stage failed. See
docs/superpowers/specs/2026-08-04-scheduled-pipeline-design.md
"""
from __future__ import annotations
import argparse
import sys
from dataclasses import dataclass

ALL_STAGES = ("fleet", "optimizers", "strings")
STAGE_BITS = {"fleet": 1, "optimizers": 2, "strings": 4}
ORCHESTRATOR_FAILED = 8          # lock held, bad arguments, or a pre-stage error

DEFAULT_TIMEOUTS = {"fleet": 45, "optimizers": 60, "strings": 30}   # minutes
STAGE_MODULES = {"optimizers": "solaranalysis.optimizers",
                 "strings": "solaranalysis.strings"}

# Statuses that set the stage's failure bit. "skipped" deliberately does not.
FAILED_STATUSES = ("failed", "timeout")


@dataclass
class StageOutcome:
    name: str
    status: str                      # ok | failed | timeout | skipped
    exit_code: int | None = None
    duration_s: float = 0.0
    detail: str = ""                 # log tail, or the runs row's error
    log_ref: str | None = None       # a second log worth reading, if any

    @property
    def failed(self) -> bool:
        return self.status in FAILED_STATUSES


def parse_only(raw: str | None) -> list[str]:
    """Stage selection, normalized to canonical run order and deduped. The
    collectors must follow the fleet snapshot so all three cover the same day,
    so the order the operator types is not the order we run."""
    if not raw:
        return list(ALL_STAGES)
    names = [s.strip() for s in raw.split(",") if s.strip()]
    unknown = [n for n in names if n not in STAGE_BITS]
    if unknown:
        raise ValueError(f"unknown stage(s): {', '.join(unknown)} "
                         f"(choose from {', '.join(ALL_STAGES)})")
    if not names:
        raise ValueError("--only was given but selected no stage")
    return [s for s in ALL_STAGES if s in names]


def aggregate_exit_code(outcomes) -> int:
    """OR the failing stages' bits. An outcome that is not one of the three
    stages — the synthetic "orchestrator" one built on the last-resort error
    path — contributes ORCHESTRATOR_FAILED rather than raising KeyError."""
    code = 0
    for o in outcomes:
        if o.failed:
            code |= STAGE_BITS.get(o.name, ORCHESTRATOR_FAILED)
    return code


class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on a usage error, which the bitmask would read as
    'optimizers failed'. Usage errors are orchestrator failures."""

    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(ORCHESTRATOR_FAILED)


def _build_parser() -> argparse.ArgumentParser:
    ap = _Parser(prog="solaranalysis.orchestrator")
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--app-dir", required=True)
    ap.add_argument("--range", default="snapshot",
                    choices=["snapshot", "30d", "12mo", "all"])
    ap.add_argument("--only", help="comma list: " + ",".join(ALL_STAGES))
    ap.add_argument("--no-email", action="store_true",
                    help="suppress every report and the failure alert")
    for stage, minutes in DEFAULT_TIMEOUTS.items():
        ap.add_argument(f"--timeout-{stage}", type=int, default=minutes,
                        metavar="MINUTES")
    ap.add_argument("--log-retention-days", type=int, default=30)
    return ap
