from solaranalysis.web import db
from solaranalysis.optimizers import store
from solaranalysis.optimizers.mappers import OptimizerInfo, OptimizerEnergyRow


def _conn():
    c = db.connect(":memory:")
    db.init_db(c)
    return c


def test_save_inventory_upserts_and_preserves_first_seen():
    conn = _conn()
    info = OptimizerInfo(serial="OPT-A", label="1.0.1", name="Optimizer 1.0.1",
                         inverter_serial="INV-1", inverter_name="Inverter 1",
                         string_label="1.0", string_name="String 1.0",
                         model="P950", status="ACTIVE")
    store.save_inventory(conn, 42, [info], now="2026-07-01T00:00:00+00:00")
    store.save_inventory(conn, 42, [info], now="2026-07-05T00:00:00+00:00")
    rows = store.load_inventory(conn, 42)
    assert len(rows) == 1
    r = rows[0]
    assert r["optimizer_serial"] == "OPT-A" and r["label"] == "1.0.1"
    assert r["inverter_serial"] == "INV-1" and r["model"] == "P950"
    assert r["first_seen_utc"] == "2026-07-01T00:00:00+00:00"   # preserved
    assert r["last_seen_utc"] == "2026-07-05T00:00:00+00:00"    # refreshed


def test_save_energy_upserts_latest_value():
    conn = _conn()
    row = OptimizerEnergyRow("INV-1", "OPT-A", energy_wh=5000.0, color=0.9, temperature_c=None)
    store.save_energy(conn, 42, "2026-07-22", [row], now="2026-07-23T00:00:00+00:00")
    row2 = OptimizerEnergyRow("INV-1", "OPT-A", energy_wh=5100.0, color=0.92, temperature_c=40.0)
    store.save_energy(conn, 42, "2026-07-22", [row2], now="2026-07-23T06:00:00+00:00")
    rows = store.load_energy(conn, 42, "2026-07-22")
    assert len(rows) == 1
    assert rows[0]["energy_wh"] == 5100.0 and rows[0]["color"] == 0.92
    assert rows[0]["temperature_c"] == 40.0


def test_energy_isolated_by_site_and_day():
    conn = _conn()
    r = OptimizerEnergyRow("INV-1", "OPT-A", 1.0, 0.5, None)
    store.save_energy(conn, 1, "2026-07-22", [r], now="n")
    store.save_energy(conn, 2, "2026-07-22", [r], now="n")
    store.save_energy(conn, 1, "2026-07-23", [r], now="n")
    assert len(store.load_energy(conn, 1, "2026-07-22")) == 1
    assert len(store.load_energy(conn, 2, "2026-07-22")) == 1
    assert len(store.load_energy(conn, 1, "2026-07-23")) == 1
