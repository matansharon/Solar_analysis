"""Pure mappers: SolarEdge /services/layout/* payloads -> plain records.

No IO, no browser — fixture/synthetic-testable. The IO shell (layout_client)
fetches the raw dicts; these turn them into rows the store persists.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class OptimizerInfo:
    serial: str
    label: str | None = None            # displayOrder, e.g. '1.4.5'
    name: str | None = None             # 'Optimizer 1.4.5'
    inverter_serial: str | None = None
    inverter_name: str | None = None
    string_label: str | None = None     # string displayOrder, e.g. '1.4'
    string_name: str | None = None      # 'String 1.4'
    model: str | None = None            # optimizer hardware model
    status: str | None = None


def flatten_inventory(payload: dict) -> list[OptimizerInfo]:
    """Walk the logical tree; emit one OptimizerInfo per OPTIMIZER leaf,
    carrying its nearest ancestor INVERTER and STRING. FOLDER nodes are
    transparent grouping containers and are recursed through."""
    root = (payload or {}).get("siteStructure", payload) or {}
    out: list[OptimizerInfo] = []

    def walk(node, inv, st):
        if not isinstance(node, dict):
            return
        t = node.get("type")
        if t == "INVERTER":
            inv = node
        elif t == "STRING":
            st = node
        elif t == "OPTIMIZER":
            props = node.get("properties") or {}
            inv = inv or {}
            st = st or {}
            out.append(OptimizerInfo(
                serial=node.get("serial"),
                label=node.get("displayOrder"),
                name=node.get("name"),
                inverter_serial=inv.get("serial"),
                inverter_name=inv.get("name"),
                string_label=st.get("displayOrder"),
                string_name=st.get("name"),
                model=props.get("model"),
                status=props.get("status"),
            ))
            return
        for child in node.get("children") or []:
            walk(child, inv, st)

    walk(root, None, None)
    return [i for i in out if i.serial]


@dataclass
class OptimizerEnergyRow:
    inverter_serial: str | None
    optimizer_serial: str
    energy_wh: float | None
    color: float | None
    temperature_c: float | None


def _num(x):
    return x if isinstance(x, (int, float)) else None


def map_by_inverter_energy(payload: dict) -> list[OptimizerEnergyRow]:
    """energy/by-inverter payload -> per-optimizer rows (energy in watt-hours)."""
    out: list[OptimizerEnergyRow] = []
    for inv in (payload or {}).get("inverters") or []:
        if not isinstance(inv, dict):
            continue
        inv_serial = inv.get("serial")
        for op in inv.get("optimizers") or []:
            if not isinstance(op, dict) or not op.get("serial"):
                continue
            energy = op.get("energy") or {}
            temp = op.get("temperature") or {}
            out.append(OptimizerEnergyRow(
                inverter_serial=inv_serial,
                optimizer_serial=op.get("serial"),
                energy_wh=_num(energy.get("value")),
                color=_num(op.get("color")),
                temperature_c=_num(temp.get("temperature")),
            ))
    return out
