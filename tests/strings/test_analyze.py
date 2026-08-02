import pytest

from solaranalysis.strings import analyze
from solaranalysis.strings.analyze import analyze_device

# 17 full days, matching the real series' usable length.
DAYS = [f"2026-07-{d:02d}" for d in range(11, 28)]
AS_OF = DAYS[-1]

# Deliberately unequal, as the real plant is: no rule may assume peer parity.
SHARES = {1: 0.50, 2: 0.30, 3: 0.20}
PAIR_VOLTS = {(1, 2): 411.2, (3, 4): 412.2}
BASE_IMB = {(1, 2): 16.0, (3, 4): 24.0}


def _channels(lifetime=None):
    lifetime = lifetime or {}
    out = [{"channel_kind": "mppt", "channel_no": ch,
            "lifetime_kwh": lifetime.get(ch, 1000.0)} for ch in SHARES]
    # individual strings carry no lifetime energy and are never MPPT rows
    out += [{"channel_kind": "string", "channel_no": n, "lifetime_kwh": None}
            for n in (1, 2, 3, 4)]
    return out


def _energy(days=DAYS, share=None, minutes=None, drop=(), total=300.0):
    """Daily energy rows. `share` overrides {(ch, day): value}; `minutes`
    overrides {day: producing_minutes}; `drop` removes (ch, day) rows."""
    share, minutes = share or {}, minutes or {}
    out = []
    for d in days:
        for ch, base in SHARES.items():
            if (ch, d) in drop:
                continue
            s = share.get((ch, d), base)
            out.append({"channel_no": ch, "day": d, "energy_kwh": s * total,
                        "share_of_total": s, "peak_w": 9000.0,
                        "producing_minutes": minutes.get(d, 800)})
    return out


def _strings(days=DAYS, imb=None, n_ts=4):
    """String samples at `n_ts` instants a day. `imb` overrides
    {(pair, day): imbalance %}. Currents are built to hit that imbalance
    exactly: i = mean * (1 +/- p/200)."""
    imb = imb or {}
    out = []
    for d in days:
        for k in range(n_ts):
            ts = f"{d} 1{k}:00:00"
            for pair, base in BASE_IMB.items():
                p = imb.get((pair, d), base)
                mean = 5.0
                for no, cur in zip(pair, (mean * (1 + p / 200),
                                          mean * (1 - p / 200))):
                    out.append({"day": d, "sampled_at": ts, "channel_no": no,
                                "voltage_v": PAIR_VOLTS[pair], "current_a": cur})
    return out


def _hourly(days=DAYS, scale=None):
    """Hourly MPPT power. `scale` multiplies {(ch, hour, day): factor}."""
    scale = scale or {}
    out = []
    for d in days:
        for h in analyze.INTRADAY_HOURS:
            for ch, s in SHARES.items():
                out.append({"day": d, "hour": h, "channel_no": ch,
                            "power_w": s * 1000.0 * scale.get((ch, h, d), 1.0)})
    return out


def _health(days=DAYS, flags=None, temps=None):
    flags, temps = flags or {}, temps or {}
    out = []
    for d in days:
        row = {"day": d}
        for col, _ in analyze._FLAG_COLUMNS:
            row[col] = flags.get((col, d), 0)
        for col in analyze.TEMP_COLUMNS:
            row[col] = temps.get((col, d), 50.0)
        out.append(row)
    return out


def run(as_of=AS_OF, channels=None, energy=None, strings=None, hourly=None,
        health=None):
    return analyze_device(
        "SN1", channels if channels is not None else _channels(),
        energy if energy is not None else _energy(), as_of,
        string_samples=strings if strings is not None else _strings(),
        hourly_rows=hourly if hourly is not None else _hourly(),
        day_health=health if health is not None else _health())


def by_label(anoms):
    return {a.label: a for a in anoms}


# --- the baseline expectation --------------------------------------------

def test_healthy_plant_is_all_clear():
    assert run() == []


def test_healthy_plant_stays_clear_with_unequal_channels_and_pairs():
    """The whole design rests on this: channels carrying 50/30/20% of output
    and pairs sitting at 16% vs 24% imbalance are normal, not anomalies."""
    out = run()
    assert out == [], [a.reason for a in out]


# --- rule 1: never-produced inputs are excluded, not flagged -------------

def test_channel_that_never_produced_is_excluded_entirely():
    chans = _channels(lifetime={3: 0.0})
    # channel 3 has no energy rows at all, which would otherwise read as dead
    energy = [r for r in _energy() if r["channel_no"] != 3]
    assert run(channels=chans, energy=energy) == []


# --- rule 2: dead / stopped reporting ------------------------------------

def test_channel_that_stopped_reporting_is_dead():
    energy = _energy(drop=[(2, DAYS[-1]), (2, DAYS[-2])])
    out = by_label(run(energy=energy))
    assert out["PV2"].severity == "dead"
    assert "stopped reporting for 2 days" in out["PV2"].reason


def test_zero_output_is_dead_and_is_not_the_same_as_a_missing_row():
    energy = _energy(share={(2, DAYS[-1]): 0.0, (2, DAYS[-2]): 0.0})
    out = by_label(run(energy=energy))
    assert out["PV2"].severity == "dead"
    assert "zero output" in out["PV2"].reason


def test_one_missing_day_is_not_yet_dead():
    assert run(energy=_energy(drop=[(2, DAYS[-1])])) == []


# --- rule 3: the inverter's own flags ------------------------------------

def test_native_string_break_flag_is_a_fault():
    out = by_label(run(health=_health(flags={("n_str_break", AS_OF): 3})))
    assert out["inverter"].severity == "fault"
    assert "string break" in out["inverter"].reason and "3 sample" in out["inverter"].reason


@pytest.mark.parametrize("col,text", list(analyze._FLAG_COLUMNS))
def test_every_native_flag_column_is_reported(col, text):
    out = run(health=_health(flags={(col, AS_OF): 1}))
    assert [a for a in out if a.severity == "fault" and text in a.reason]


def test_native_flags_still_fire_on_a_day_too_short_for_other_rules():
    """Rule 3 needs no history, so an incomplete day must not suppress it."""
    out = run(energy=_energy(minutes={AS_OF: 120}),
              health=_health(flags={("n_str_break", AS_OF): 1}))
    sev = sorted(a.severity for a in out)
    assert sev == ["fault", "watch"]
    assert any("day incomplete" in a.reason for a in out)


# --- the short-day guard --------------------------------------------------

def test_incomplete_day_reports_a_notice_instead_of_a_false_all_clear():
    out = run(energy=_energy(minutes={AS_OF: 200}))
    assert len(out) == 1 and out[0].severity == "watch"
    assert "day incomplete" in out[0].reason and "200 producing minutes" in out[0].reason


def test_a_missing_as_of_day_is_reported_as_incomplete():
    energy = [r for r in _energy() if r["day"] != AS_OF]
    out = run(energy=energy)
    assert len(out) == 1 and "day incomplete" in out[0].reason


def test_short_days_are_excluded_from_the_history_a_rule_compares_against():
    """A truncated day must not drag a channel's baseline down: paging is
    newest-first, so its stored share is fine but its samples are evening-only.
    Here the short day carries an absurd share and must simply be ignored."""
    minutes = {DAYS[3]: 100}
    share = {(1, DAYS[3]): 0.05}
    assert run(energy=_energy(share=share, minutes=minutes)) == []


def test_usable_days_filters_on_the_longest_channel_and_stops_at_as_of():
    rows = _energy(days=DAYS[:3], minutes={DAYS[1]: 100})
    assert analyze.usable_days(rows, DAYS[-1]) == [DAYS[0], DAYS[2]]
    assert analyze.usable_days(rows, DAYS[0]) == [DAYS[0]]


# --- rule 4: share collapse ----------------------------------------------

def test_share_collapse_is_flagged_against_the_channels_own_median():
    out = by_label(run(energy=_energy(share={(3, AS_OF): 0.20 * 0.88})))
    assert out["PV3"].severity == "underperforming"
    assert "below its own" in out["PV3"].reason
    assert out["PV3"].baseline == pytest.approx(0.20)
    assert out["PV3"].share_of_total == pytest.approx(0.176)


def test_share_drop_just_under_the_threshold_does_not_fire():
    just_over = 1 - (analyze.SHARE_DROP_REL - 0.005)
    assert run(energy=_energy(share={(3, AS_OF): 0.20 * just_over})) == []


def test_share_rules_skip_rather_than_fire_below_minimum_history():
    days = DAYS[:analyze.MIN_HISTORY_DAYS]      # as_of has only 6 prior days
    out = run(as_of=days[-1], energy=_energy(days=days, share={(3, days[-1]): 0.10}),
              strings=_strings(days), hourly=_hourly(days), health=_health(days))
    assert out == []


def test_share_rule_fires_as_soon_as_minimum_history_exists():
    days = DAYS[:analyze.MIN_HISTORY_DAYS + 1]
    out = run(as_of=days[-1], energy=_energy(days=days, share={(3, days[-1]): 0.10}),
              strings=_strings(days), hourly=_hourly(days), health=_health(days))
    assert by_label(out)["PV3"].severity == "underperforming"


# --- rule 5: persistent mild underperformance ----------------------------

def test_persistent_mild_underperformance_is_flagged():
    lo = 0.20 * (1 - analyze.SHARE_WATCH_REL - 0.005)
    share = {(3, d): lo for d in DAYS[-3:]}
    out = by_label(run(energy=_energy(share=share)))
    assert out["PV3"].severity == "underperforming"
    assert f"on {analyze.SHARE_WATCH_MIN_DAYS}/{analyze.SHARE_WATCH_WINDOW} days" \
        in out["PV3"].reason


def test_two_mild_days_out_of_five_do_not_fire():
    lo = 0.20 * (1 - analyze.SHARE_WATCH_REL - 0.005)
    assert run(energy=_energy(share={(3, d): lo for d in DAYS[-2:]})) == []


# --- rule 6: string-pair imbalance ---------------------------------------

def test_pair_imbalance_is_flagged_against_that_pairs_own_median():
    out = by_label(run(strings=_strings(imb={((1, 2), AS_OF): 16.0 + 5.0})))
    a = out["strings 1+2"]
    assert a.severity == "imbalance" and a.scope == "pair"
    assert a.metric == pytest.approx(21.0) and a.baseline == pytest.approx(16.0)


def test_a_permanently_high_but_steady_pair_is_never_flagged():
    """Pair (3,4) sits at 24% forever on the real plant. An absolute threshold
    would flag it daily; comparing it to itself must not."""
    assert [a for a in run() if a.label == "strings 3+4"] == []


def test_pair_imbalance_below_the_deviation_threshold_does_not_fire():
    nudge = analyze.PAIR_DEV_PP - 0.5
    assert run(strings=_strings(imb={((1, 2), AS_OF): 16.0 + nudge})) == []


def test_pair_imbalance_fires_on_an_improvement_too():
    """A pair that suddenly matches better than it ever has is also a change
    worth a look -- one string's current collapsing can close the gap."""
    out = by_label(run(strings=_strings(imb={((1, 2), AS_OF): 16.0 - 5.0})))
    assert out["strings 1+2"].metric == pytest.approx(11.0)
    assert "-5.0pp" in out["strings 1+2"].reason


def test_pairs_come_from_sample_voltage_not_from_the_channel_snapshot():
    rows = [{"day": "d", "sampled_at": "t", "channel_no": 1,
             "voltage_v": 400.0, "current_a": 10.0},
            {"day": "d", "sampled_at": "t", "channel_no": 2,
             "voltage_v": 400.04, "current_a": 8.0},     # rounds to 400.0
            {"day": "d", "sampled_at": "t", "channel_no": 3,
             "voltage_v": 380.0, "current_a": 9.0}]
    got = analyze.pair_imbalance_by_day(rows)
    assert set(got["d"]) == {(1, 2)}                     # 3 is unpaired, skipped
    assert got["d"][(1, 2)] == pytest.approx(abs(10 - 8) / 9 * 100)


def test_a_voltage_group_that_is_not_a_pair_is_skipped_not_guessed():
    rows = [{"day": "d", "sampled_at": "t", "channel_no": n,
             "voltage_v": 400.0, "current_a": 10.0} for n in (1, 2, 3, 4)]
    assert analyze.pair_imbalance_by_day(rows) == {}


def test_pair_imbalance_takes_the_median_across_the_days_samples():
    rows = []
    for ts, (a, b) in zip("xyz", [(11.0, 9.0), (10.5, 9.5), (30.0, 1.0)]):
        rows += [{"day": "d", "sampled_at": ts, "channel_no": 1,
                  "voltage_v": 400.0, "current_a": a},
                 {"day": "d", "sampled_at": ts, "channel_no": 2,
                  "voltage_v": 400.0, "current_a": b}]
    # 20%, 10%, 187% -> median 20%, so one wild instant cannot carry the day
    assert analyze.pair_imbalance_by_day(rows)["d"][(1, 2)] == pytest.approx(20.0)


def test_pair_imbalance_ignores_rows_with_missing_current_or_voltage():
    rows = [{"day": "d", "sampled_at": "t", "channel_no": 1,
             "voltage_v": 400.0, "current_a": None},
            {"day": "d", "sampled_at": "t", "channel_no": 2,
             "voltage_v": None, "current_a": 8.0}]
    assert analyze.pair_imbalance_by_day(rows) == {}


# --- rule 7: degrading ----------------------------------------------------

# Rule 7 sits behind rules 4 and 5, which `continue` before it. Rule 5 (2% below
# the trailing median on 3 of the last 5 days) is strictly more sensitive than
# rule 7 (3% between two 7-day means), so **any decline that is still present
# today trips rule 4 or 5 first** -- see the shadowing test below, which pins
# that rather than leaving it to be rediscovered.
#
# What reaches rule 7 is therefore the one shape rules 4/5 cannot see: a dip in
# the older half of the recent-7 window that has since recovered, so today looks
# normal but the 7-day mean does not.

def _recovered_dip(depth=0.80):
    """A dip 5-7 days back that has since recovered -- the only shape that
    reaches rule 7, because today's share is normal."""
    return {(3, d): 0.20 * depth
            for d in DAYS[-analyze.DEGRADE_RECENT:-analyze.SHARE_WATCH_WINDOW + 1]}


def test_degrading_is_flagged_when_a_recovered_dip_drags_the_seven_day_mean():
    out = by_label(run(energy=_energy(share=_recovered_dip())))
    assert out["PV3"].severity == "degrading"
    assert f"{analyze.DEGRADE_RECENT}-day mean share" in out["PV3"].reason
    assert "against the prior" in out["PV3"].reason
    # today itself is healthy -- this is exactly why rules 4/5 stayed silent
    assert out["PV3"].share_of_total == pytest.approx(0.20)


def test_a_decline_still_present_today_is_rule_4_or_5_not_rule_7():
    """Documents that rule 7 is shadowed for progressive decline: rule 5 is the
    more sensitive of the two, so it always speaks first. Rule 7 therefore adds
    no detection over rule 5 -- it only labels the recovered-dip case above."""
    for pct in (0.03, 0.05, 0.10):
        share = {(3, d): 0.20 * (1 - pct)
                 for d in DAYS[-analyze.DEGRADE_RECENT:]}
        out = by_label(run(energy=_energy(share=share)))
        assert out["PV3"].severity == "underperforming", pct


def test_degrading_needs_two_full_windows_of_history():
    days = DAYS[:analyze.DEGRADE_MIN_HISTORY - 1]
    share = {(3, d): 0.20 * 0.80
             for d in days[-analyze.DEGRADE_RECENT:-analyze.SHARE_WATCH_WINDOW + 1]}
    out = run(as_of=days[-1], energy=_energy(days=days, share=share),
              strings=_strings(days), hourly=_hourly(days), health=_health(days))
    # the same shape that fires above must stay silent one day short of 14
    assert [a for a in out if a.label == "PV3"] == []


# --- rule 8: intraday shape ----------------------------------------------

def test_contiguous_intraday_collapse_is_flagged_with_its_window():
    scale = {(2, h, AS_OF): 0.5 for h in (8, 9, 10)}
    out = by_label(run(hourly=_hourly(scale=scale)))
    assert out["PV2"].severity == "watch"
    assert "08:00-11:00" in out["PV2"].reason and "shading or soiling" in out["PV2"].reason


def test_a_single_bad_hour_is_a_cloud_not_an_anomaly():
    assert run(hourly=_hourly(scale={(2, 9, AS_OF): 0.4})) == []


def test_scattered_bad_hours_do_not_form_a_run():
    scale = {(2, h, AS_OF): 0.4 for h in (8, 10, 12, 14)}
    assert run(hourly=_hourly(scale=scale)) == []


def test_intraday_window_is_reported_from_the_longest_run_only():
    scale = {(2, h, AS_OF): 0.4 for h in (7, 8, 9, 15)}
    out = by_label(run(hourly=_hourly(scale=scale)))
    assert "07:00-10:00" in out["PV2"].reason and "3h" in out["PV2"].reason


def test_hourly_share_normalizes_within_the_hour():
    rows = [{"day": "d", "hour": 9, "channel_no": 1, "power_w": 300.0},
            {"day": "d", "hour": 9, "channel_no": 2, "power_w": 100.0},
            {"day": "d", "hour": 10, "channel_no": 1, "power_w": 30.0},
            {"day": "d", "hour": 10, "channel_no": 2, "power_w": 10.0}]
    got = analyze.hourly_share_by_day(rows)
    # the whole plant dropping 10x between hours leaves both shares unchanged
    assert got[1][9]["d"] == pytest.approx(0.75)
    assert got[1][10]["d"] == pytest.approx(0.75)


def test_longest_run_picks_the_longest_not_the_first():
    assert analyze._longest_run([7, 9, 10, 11, 14]) == (3, 9, 11)
    assert analyze._longest_run([]) == (0, 0, 0)
    assert analyze._longest_run([5]) == (1, 5, 5)


# --- rule 9: temperature --------------------------------------------------

def test_temperature_spike_is_flagged_against_that_sensors_own_history():
    hot = 50.0 + analyze.TEMP_RISE_C + 4.0
    out = run(health=_health(temps={("temp3_c", AS_OF): hot}))
    a = [x for x in out if "temp3_c" in x.reason][0]
    assert a.severity == "watch" and a.metric == pytest.approx(hot)


def test_a_warm_day_inside_the_band_does_not_fire():
    warm = 50.0 + analyze.TEMP_RISE_C - 1.0
    assert run(health=_health(temps={("temp3_c", AS_OF): warm})) == []


def test_the_absent_temp4_sensor_is_never_consulted():
    assert "temp4_c" not in analyze.TEMP_COLUMNS
    # a health row carrying a wild temp4_c must change nothing
    health = _health()
    for row in health:
        row["temp4_c"] = 0.0
    health[-1]["temp4_c"] = 900.0
    assert run(health=health) == []


# --- ordering -------------------------------------------------------------

def test_findings_are_sorted_worst_first():
    out = run(energy=_energy(drop=[(2, DAYS[-1]), (2, DAYS[-2])]),
              strings=_strings(imb={((1, 2), AS_OF): 30.0}),
              health=_health(flags={("n_str_break", AS_OF): 1},
                             temps={("temp3_c", AS_OF): 70.0}))
    assert [a.severity for a in out] == ["dead", "fault", "imbalance", "watch"]
