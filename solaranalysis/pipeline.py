from __future__ import annotations
from datetime import datetime, timezone
from .config import AppConfig, PlantConfig
from .core.schema import PlantData, TimeRange, Metric
from .core import units
from .core.validate import validate_plant
from .core.analyze import run_analysis, build_data_block, verify_numbers, default_meta
from .adapters.base import get_adapter

def _normalize(pd: PlantData, pc: PlantConfig) -> PlantData:
    kwp = pd.peak_power_kwp.value
    life = pd.energy_lifetime_kwh.value
    sy = units.specific_yield(life, kwp)
    pd.specific_yield_kwh_per_kwp = Metric(units.round_opt(sy), "kWh/kWp", is_derived=True)
    if pc.tariff_per_kwh is not None:
        pd.savings = Metric(units.round_opt(units.money(life, pc.tariff_per_kwh)),
                            "currency", is_derived=True)
    return validate_plant(pd)

def run_pipeline(cfg: AppConfig, time_range: TimeRange, session_store,
                 adapter_factory=get_adapter, analyzer=run_analysis,
                 progress=None) -> dict:
    def emit(**ev):
        if progress:
            progress(ev)
    plants: list[PlantData] = []
    skipped: list[dict] = []
    for pc in cfg.plants:
        emit(event="plant_start", plant=pc.name)
        try:
            adapter = adapter_factory(pc.auth, session_store)
            emit(event="plant_step", plant=pc.name, step="login")
            adapter.login()
            emit(event="plant_step", plant=pc.name, step="fetch")
            fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for pd in adapter.fetch(time_range):
                if pc.currency and not pd.currency:
                    pd.currency = pc.currency
                pd.fetched_at_utc = fetched_at
                plants.append(_normalize(pd, pc))
            emit(event="plant_done", plant=pc.name, ok=True)
        except Exception as e:  # isolate per-plant failures
            print(f"[warn] plant {pc.name!r} unavailable: {e}")
            skipped.append({"name": pc.name, "reason": str(e)})
            emit(event="plant_done", plant=pc.name, ok=False, reason=str(e))
    emit(event="analyze_start")
    report_md = analyzer(plants, time_range, cfg) if plants else "No plant data available."
    data_block = build_data_block(plants, time_range, default_meta(plants))
    return {"report_md": report_md, "plants": plants,
            "verify_missing": verify_numbers(report_md, data_block),
            "skipped_plants": skipped}
