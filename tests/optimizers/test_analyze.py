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
