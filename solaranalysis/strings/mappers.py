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
        lifetime = None
        for r in rows:                      # any row carries the lifetime counter
            lifetime = _num(r.get(f"epv{n}Total"))
            if lifetime is not None:
                break
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
