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
