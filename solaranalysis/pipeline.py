from __future__ import annotations
from .config import AppConfig
from .core.schema import PlantData, TimeRange, Metric
from .core import units
from .core.validate import validate_plant
from .core.analyze import run_analysis, build_data_block, verify_numbers
from .adapters.base import get_adapter

def _normalize(pd: PlantData) -> PlantData:
    kwp = pd.peak_power_kwp.value
    life = pd.energy_lifetime_kwh.value
    sy = units.specific_yield(life, kwp)
    pd.specific_yield_kwh_per_kwp = Metric(units.round_opt(sy), "kWh/kWp", is_derived=True)
    return validate_plant(pd)

def run_pipeline(cfg: AppConfig, time_range: TimeRange, session_store,
                 adapter_factory=get_adapter, analyzer=run_analysis) -> dict:
    plants: list[PlantData] = []
    skipped: list[dict] = []
    for pc in cfg.plants:
        try:
            adapter = adapter_factory(pc.auth, session_store)
            adapter.login()
            for pd in adapter.fetch(time_range):
                if pc.currency and not pd.currency:
                    pd.currency = pc.currency
                plants.append(_normalize(pd))
        except Exception as e:  # isolate per-plant failures
            print(f"[warn] plant {pc.name!r} unavailable: {e}")
            skipped.append({"name": pc.name, "reason": str(e)})
    report_md = analyzer(plants, time_range, cfg) if plants else "No plant data available."
    data_block = build_data_block(plants, time_range,
                                  {"currency": plants[0].currency if plants else None})
    return {"report_md": report_md, "plants": plants,
            "verify_missing": verify_numbers(report_md, data_block),
            "skipped_plants": skipped}
