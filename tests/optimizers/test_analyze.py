from solaranalysis.optimizers.analyze import analyze_site, OptimizerAnomaly


def _inv(*serials, string="1.0"):
    return [{"optimizer_serial": s, "label": f"1.0.{i+1}", "string_label": string}
            for i, s in enumerate(serials)]


def _rows(serial, days_vals):
    # days_vals: list of (day, energy_wh, color)
    return [{"optimizer_serial": serial, "day": d, "energy_wh": e, "color": c}
            for d, e, c in days_vals]


DAYS = ["2026-07-18", "2026-07-19", "2026-07-20", "2026-07-21", "2026-07-22"]


def test_healthy_optimizers_not_flagged():
    inv = _inv("A", "B", "C")
    rows = []
    for s in ("A", "B", "C"):
        rows += _rows(s, [(d, 5000.0, 0.97) for d in DAYS])
    assert analyze_site(1, inv, rows, "2026-07-22") == []


def test_dead_optimizer_flagged():
    inv = _inv("A", "B", "C")
    rows = _rows("A", [(d, 5000.0, 0.97) for d in DAYS[:3]]
                 + [("2026-07-21", 0.0, 0.0), ("2026-07-22", 0.0, 0.0)])
    for s in ("B", "C"):
        rows += _rows(s, [(d, 5000.0, 0.97) for d in DAYS])
    out = analyze_site(1, inv, rows, "2026-07-22")
    a = {x.optimizer_serial: x for x in out}
    assert a["A"].severity == "dead"
    assert "B" not in a and "C" not in a


def test_underperformer_by_color_persistence():
    inv = _inv("A", "B", "C")
    # A: color below 0.60 on 3 of last 5 days
    rows = _rows("A", [("2026-07-18", 5000.0, 0.97), ("2026-07-19", 2000.0, 0.50),
                       ("2026-07-20", 2000.0, 0.45), ("2026-07-21", 2000.0, 0.55),
                       ("2026-07-22", 5000.0, 0.97)])
    for s in ("B", "C"):
        rows += _rows(s, [(d, 5000.0, 0.97) for d in DAYS])
    out = analyze_site(1, inv, rows, "2026-07-22")
    a = {x.optimizer_serial: x for x in out}
    assert a["A"].severity == "underperforming"
    assert "color" in a["A"].reason


def test_underperformer_by_string_median():
    inv = _inv("A", "B", "C")  # same string 1.0
    # A produces ~50% of peers on 3+ of last 5 days but color stays okay
    rows = _rows("A", [(d, 2500.0, 0.80) for d in DAYS])
    for s in ("B", "C"):
        rows += _rows(s, [(d, 5000.0, 0.97) for d in DAYS])
    out = analyze_site(1, inv, rows, "2026-07-22")
    a = {x.optimizer_serial: x for x in out}
    assert a["A"].severity == "underperforming"
    assert a["A"].ratio_to_string is not None and a["A"].ratio_to_string < 0.7


def test_watch_single_day_dip():
    inv = _inv("A", "B", "C")
    rows = _rows("A", [(d, 5000.0, 0.97) for d in DAYS[:4]] + [("2026-07-22", 4000.0, 0.70)])
    for s in ("B", "C"):
        rows += _rows(s, [(d, 5000.0, 0.97) for d in DAYS])
    out = analyze_site(1, inv, rows, "2026-07-22")
    a = {x.optimizer_serial: x for x in out}
    assert a["A"].severity == "watch"  # single-day color<0.75, not persistent


def _n_days(n, end="2026-07-22"):
    from datetime import date, timedelta
    e = date.fromisoformat(end)
    return [(e - timedelta(days=n - 1 - i)).isoformat() for i in range(n)]


def test_degrading_optimizer_flagged_when_declining_faster_than_string():
    inv = _inv("A", "B", "C")
    days = _n_days(14)
    rows = []
    # B, C: flat ~5000 across all 14 days (string baseline stable)
    for s in ("B", "C"):
        rows += _rows(s, [(d, 5000.0, 0.95) for d in days])
    # A: prior 7 days ~5000, recent 7 days ~3800 (24% drop) while peers flat,
    #    color stays >=0.75 so it is not caught as underperforming/watch.
    a_vals = [(d, 5000.0, 0.95) for d in days[:7]] + [(d, 3800.0, 0.95) for d in days[7:]]
    rows += _rows("A", a_vals)
    out = {x.optimizer_serial: x for x in analyze_site(1, inv, rows, "2026-07-22")}
    assert "A" in out and out["A"].severity == "degrading"
    assert "B" not in out and "C" not in out


def test_no_degrading_without_enough_history():
    inv = _inv("A", "B")
    days = _n_days(6)  # < DEGRADE_MIN_HISTORY
    rows = _rows("A", [(d, 5000.0, 0.95) for d in days[:3]] + [(d, 3000.0, 0.95) for d in days[3:]])
    rows += _rows("B", [(d, 5000.0, 0.95) for d in days])
    out = {x.optimizer_serial: x for x in analyze_site(1, inv, rows, "2026-07-22")}
    assert "A" not in out  # not enough history to judge a trend


def test_silent_optimizer_flagged_dead():
    # A reports through 07-20 then vanishes from the payload entirely. A
    # missing row is not a zero row, so the ~zero-output rule cannot see it.
    inv = _inv("A", "B", "C")
    rows = _rows("A", [(d, 5000.0, 0.97) for d in DAYS[:3]])
    for s in ("B", "C"):
        rows += _rows(s, [(d, 5000.0, 0.97) for d in DAYS])
    out = {x.optimizer_serial: x for x in analyze_site(1, inv, rows, "2026-07-22")}
    assert out["A"].severity == "dead"
    assert "stopped reporting" in out["A"].reason
    assert "B" not in out and "C" not in out


def test_optimizer_with_no_rows_at_all_flagged_dead():
    inv = _inv("A", "B", "C")
    rows = []
    for s in ("B", "C"):
        rows += _rows(s, [(d, 5000.0, 0.97) for d in DAYS])
    out = {x.optimizer_serial: x for x in analyze_site(1, inv, rows, "2026-07-22")}
    assert out["A"].severity == "dead"
    assert out["A"].latest_day is None


def test_single_missing_day_is_not_dead():
    # One absent day is a collection hiccup, not a fault — needs DEAD_DAYS.
    inv = _inv("A", "B", "C")
    rows = _rows("A", [(d, 5000.0, 0.97) for d in DAYS[:4]])
    for s in ("B", "C"):
        rows += _rows(s, [(d, 5000.0, 0.97) for d in DAYS])
    out = {x.optimizer_serial: x for x in analyze_site(1, inv, rows, "2026-07-22")}
    assert "A" not in out
