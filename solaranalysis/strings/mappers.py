"""Pure mappers: Growatt getMAXHistory / getDevicesByPlantList payloads ->
plain records.

No IO, no browser, no clock — fixture-testable. The IO shell (history_client)
fetches the raw dicts; these turn them into rows the store persists.

Two granularity tiers exist in a sample row, and only the first has energy:
  * MPPT inputs 1..16  -- ipvN (A), vpvN (V), ppvN (W), epvNToday/epvNTotal (kWh)
  * individual strings 1..32 -- currentStringN (A), vStringN (V), no energy
Parallel strings on one MPPT necessarily share a voltage, so exactly-equal
vStringN values group them; see spec section 3(b).
"""
from __future__ import annotations
from dataclasses import dataclass

MPPT_MAX = 16
STRING_MAX = 32
SAMPLE_MINUTES = 5      # the portal's fixed 5-minute logging cadence


def _num(x):
    """JSON number or numeric string -> float; anything else -> None.

    The portal is inconsistent about which fields are quoted, so every numeric
    read goes through here.
    """
    if isinstance(x, bool) or x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    try:
        return float(str(x).strip())
    except (TypeError, ValueError):
        return None


def _text(x):
    """Preserve a code/flag token verbatim (stored as TEXT), or None."""
    return None if x is None else str(x)


@dataclass
class InverterInfo:
    serial: str
    model: str | None = None
    datalog_sn: str | None = None
    nominal_power_w: float | None = None
    plant_id: str | None = None


def parse_devices(payload: dict) -> list[InverterInfo]:
    """getDevicesByPlantList payload -> the inverters on the plant."""
    out: list[InverterInfo] = []
    for r in ((payload or {}).get("obj") or {}).get("datas") or []:
        if not isinstance(r, dict) or not r.get("sn"):
            continue
        out.append(InverterInfo(
            serial=str(r["sn"]),
            model=r.get("deviceModel") or r.get("alias"),
            datalog_sn=r.get("datalogSn"),
            nominal_power_w=_num(r.get("nominalPower")),
            plant_id=None if r.get("plantId") is None else str(r["plantId"]),
        ))
    return out


def sample_day(row: dict) -> str | None:
    """Plant-local 'YYYY-MM-DD' from the row's `time` field.

    Deliberately ignores the sibling `calendar` object, whose `month` is
    0-based (July arrives as 6).
    """
    t = str((row or {}).get("time") or "")
    day = t[:10]
    if len(day) != 10 or day[4] != "-" or day[7] != "-":
        return None
    return day


@dataclass
class ChannelInfo:
    kind: str                        # 'mppt' | 'string'
    no: int
    parent_no: int | None = None     # string -> MPPT input; see below
    group_voltage: float | None = None
    lifetime_kwh: float | None = None   # MPPT inputs only (epvNTotal)


def best_row(rows: list[dict]) -> dict:
    """The day's highest-`pac` sample — the reading closest to solar peak.

    Night rows carry zeros for every voltage and current, so anything that
    needs a live electrical picture keys off this row rather than the first.
    """
    best, best_pac = {}, None
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        pac = _num(r.get("pac"))
        if pac is not None and (best_pac is None or pac > best_pac):
            best, best_pac = r, pac
    if not best:
        for r in rows or []:
            if isinstance(r, dict):
                return r
    return best


def _lifetime_kwh(rows: list[dict], n: int) -> float | None:
    """The MAXIMUM non-None `epv{n}Total` across the day's rows -- a lifetime
    counter only ever climbs, so the max is the day's true value regardless of
    row order. Rows arrive newest-first from the collector, but the fixture
    and live payloads aren't guaranteed strictly chronological, so taking the
    FIRST non-None value (the old behaviour) could land on any row's reading
    rather than the true maximum.

    Returns None when the key is absent from every row, 0.0 when present but
    the input has never produced -- callers treat both as "falsy" to skip.
    """
    best = None
    for r in rows:
        v = _num(r.get(f"epv{n}Total"))
        if v is not None and (best is None or v > best):
            best = v
    return best


def channel_inventory(rows: list[dict]) -> list[ChannelInfo]:
    """Which channels are live, from a day's sample rows.

    An MPPT input counts as live once it has produced anything ever
    (`epvNTotal > 0`); this rejects unused inputs that still float an
    open-circuit voltage. An individual string counts as live when it carries
    current at the day's peak; this rejects strings hanging off a dead input.

    `parent_no` (string -> MPPT) is left None on purpose: pair-current sums do
    not cleanly biject to `ipvN` on this hardware, so any mapping would be a
    guess. Grouping parallel strings by exactly-equal `group_voltage` is
    reliable and is what the Phase C2 imbalance rule uses.
    """
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return []
    peak = best_row(rows)
    out: list[ChannelInfo] = []

    for n in range(1, MPPT_MAX + 1):
        lifetime = _lifetime_kwh(rows, n)
        if not lifetime:                    # None or 0.0 -> never produced
            continue
        out.append(ChannelInfo(kind="mppt", no=n,
                               group_voltage=_num(peak.get(f"vpv{n}")),
                               lifetime_kwh=lifetime))

    for n in range(1, STRING_MAX + 1):
        current = _num(peak.get(f"currentString{n}"))
        if not current:                     # None or 0.0 -> not carrying current
            continue
        out.append(ChannelInfo(kind="string", no=n,
                               group_voltage=_num(peak.get(f"vString{n}"))))
    return out


@dataclass
class ChannelDayEnergy:
    channel_no: int
    energy_kwh: float | None = None
    share_of_total: float | None = None
    peak_w: float | None = None
    peak_at: str | None = None
    producing_minutes: int = 0


def map_day_energy(rows: list[dict]) -> list[ChannelDayEnergy]:
    """A day's sample rows -> one record per MPPT input that has ever produced.

    `epvNToday` is a counter that resets at midnight, so the day's total is its
    MAXIMUM over the day rather than blindly the newest sample — that stays
    correct if a datalogger reconnect ever restates it.

    Channels with `epvNTotal == 0` never produced and are skipped entirely; a
    channel that HAS produced before but made nothing today still gets an
    explicit 0.0 row, because a missing row is not a zero row.
    """
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return []

    out: list[ChannelDayEnergy] = []
    for n in range(1, MPPT_MAX + 1):
        lifetime = _lifetime_kwh(rows, n)
        if not lifetime:
            continue

        energy, peak_w, peak_at, producing = None, None, None, 0
        for r in rows:
            e = _num(r.get(f"epv{n}Today"))
            if e is not None and (energy is None or e > energy):
                energy = e
            p = _num(r.get(f"ppv{n}"))
            if p is not None and p > 0:
                producing += 1
                if peak_w is None or p > peak_w:
                    peak_w, peak_at = p, r.get("time")
        out.append(ChannelDayEnergy(
            channel_no=n, energy_kwh=energy, peak_w=peak_w, peak_at=peak_at,
            producing_minutes=producing * SAMPLE_MINUTES))

    total = sum(r.energy_kwh for r in out if r.energy_kwh is not None)
    for r in out:
        if total and r.energy_kwh is not None:
            r.share_of_total = r.energy_kwh / total
    return out


@dataclass
class ChannelSample:
    sampled_at: str
    day: str
    kind: str
    no: int
    power_w: float | None = None
    voltage_v: float | None = None
    current_a: float | None = None


_FIELDS = {
    "mppt": ("ppv{n}", "vpv{n}", "ipv{n}"),
    "string": (None, "vString{n}", "currentString{n}"),   # no per-string power
}


def map_channel_samples(rows: list[dict],
                        channels: list[ChannelInfo]) -> list[ChannelSample]:
    """A day's rows x the live inventory -> the intraday per-channel series.

    `channels` is a required filter: the payload carries all 16 MPPT and all 32
    string keys with zero values, so emitting them all would store 48 rows per
    sample instead of the 18 that are real hardware.
    """
    out: list[ChannelSample] = []
    live = [c for c in (channels or []) if c.kind in _FIELDS]
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        day = sample_day(r)
        at = r.get("time")
        if not day or not at:
            continue
        for c in live:
            p_key, v_key, i_key = _FIELDS[c.kind]
            out.append(ChannelSample(
                sampled_at=str(at), day=day, kind=c.kind, no=c.no,
                power_w=None if p_key is None else _num(r.get(p_key.format(n=c.no))),
                voltage_v=_num(r.get(v_key.format(n=c.no))),
                current_a=_num(r.get(i_key.format(n=c.no)))))
    return out


@dataclass
class InverterSample:
    sampled_at: str
    day: str
    pac_w: float | None = None
    e_ac_today_kwh: float | None = None
    temp_c: float | None = None
    temp2_c: float | None = None
    temp3_c: float | None = None
    temp4_c: float | None = None
    temp5_c: float | None = None
    pv_iso_kohm: float | None = None
    gfci_ma: float | None = None
    status: str | None = None
    derating_mode: str | None = None
    str_break: str | None = None
    str_unbalance: str | None = None
    str_unmatch: str | None = None
    warn_code: str | None = None
    fault_code1: str | None = None
    fault_code2: str | None = None
    fault_type: str | None = None


def map_inverter_samples(rows: list[dict]) -> list[InverterSample]:
    """A day's rows -> whole-inverter health and native string diagnostics.

    `StrBreak` / `StrUnblance` (Growatt's spelling) / `StrUnmatch` are the
    inverter's OWN string-fault verdicts, so they are authoritative when
    non-zero. Codes are kept as text to preserve the raw token.
    """
    out: list[InverterSample] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        day = sample_day(r)
        at = r.get("time")
        if not day or not at:
            continue
        out.append(InverterSample(
            sampled_at=str(at), day=day,
            pac_w=_num(r.get("pac")),
            e_ac_today_kwh=_num(r.get("eacToday")),
            temp_c=_num(r.get("temperature")),
            temp2_c=_num(r.get("temperature2")),
            temp3_c=_num(r.get("temperature3")),
            temp4_c=_num(r.get("temperature4")),
            temp5_c=_num(r.get("temperature5")),
            pv_iso_kohm=_num(r.get("pvIso")),
            gfci_ma=_num(r.get("gfci")),
            status=_text(r.get("status")),
            derating_mode=_text(r.get("deratingMode")),
            str_break=_text(r.get("StrBreak")),
            str_unbalance=_text(r.get("StrUnblance")),
            str_unmatch=_text(r.get("StrUnmatch")),
            warn_code=_text(r.get("warnCode")),
            fault_code1=_text(r.get("faultCode1")),
            fault_code2=_text(r.get("faultCode2")),
            fault_type=_text(r.get("faultType")),
        ))
    return out
