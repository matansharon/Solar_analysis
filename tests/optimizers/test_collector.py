from datetime import date

from solaranalysis.web import db
from solaranalysis.optimizers import collector, store


def test_parse_site_ids():
    payload = {"page": [{"solarFieldId": 2387929}, {"solarFieldId": 2257529},
                        {"noId": 1}]}
    assert collector.parse_site_ids(payload) == [2387929, 2257529]
    assert collector.parse_site_ids({}) == []


def test_day_range_counts_back_from_target():
    assert collector.day_range(date(2026, 7, 22), 1) == ["2026-07-22"]
    assert collector.day_range(date(2026, 7, 22), 3) == [
        "2026-07-20", "2026-07-21", "2026-07-22"]


class FakeBS:
    """Returns canned tree + energy payloads keyed by URL fragment."""
    def __init__(self):
        self.tree = {"siteStructure": {"type": "SITE", "children": [
            {"type": "FOLDER", "name": "INVERTER", "children": [
                {"type": "INVERTER", "serial": "INV-1", "name": "Inverter 1",
                 "properties": {"model": "SE50K"}, "children": [
                    {"type": "FOLDER", "name": "STRING", "children": [
                        {"type": "STRING", "name": "String 1.0", "displayOrder": "1.0",
                         "children": [{"type": "FOLDER", "name": "OPTIMIZER", "children": [
                             {"type": "OPTIMIZER", "serial": "OPT-A", "displayOrder": "1.0.1",
                              "name": "Optimizer 1.0.1", "properties": {"model": "P950"}}]}]}]}]}]}]}}
        self.energy = {"inverters": [{"serial": "INV-1", "optimizers": [
            {"serial": "OPT-A", "energy": {"value": 5000.0, "unit": "watt-hour"},
             "color": 0.9, "temperature": {"temperature": None}}]}]}
    def get_json(self, url):
        if "/logical/" in url:
            return self.tree
        if "/by-inverter" in url:
            return self.energy
        return {}


def test_collect_site_persists_inventory_and_energy():
    conn = db.connect(":memory:"); db.init_db(conn)
    res = collector.collect_site(FakeBS(), conn, site_id=42,
                                 days=["2026-07-21", "2026-07-22"], now="NOW")
    assert res["optimizers"] == 1 and res["days"] == 2 and res["energy_rows"] == 2
    inv = store.load_inventory(conn, 42)
    assert len(inv) == 1 and inv[0]["optimizer_serial"] == "OPT-A"
    e21 = store.load_energy(conn, 42, "2026-07-21")
    e22 = store.load_energy(conn, 42, "2026-07-22")
    assert e21[0]["energy_wh"] == 5000.0 and e22[0]["color"] == 0.9


def test_collect_isolates_per_site_failure():
    conn = db.connect(":memory:"); db.init_db(conn)

    class Boom(FakeBS):
        def get_json(self, url):
            raise RuntimeError("network down")

    results = collector.collect(
        bs_for={42: FakeBS(), 99: Boom()}, conn=conn,
        site_ids=[42, 99], days=["2026-07-22"], now="NOW")
    ok = {r["site_id"]: r for r in results}
    assert ok[42]["optimizers"] == 1
    assert "error" in ok[99]
    # site 42 still persisted despite site 99 blowing up
    assert len(store.load_inventory(conn, 42)) == 1


def test_collect_rolls_back_partial_write_on_failure():
    conn = db.connect(":memory:"); db.init_db(conn)

    class PartialFail(FakeBS):
        """Saves inventory (via /logical/) then blows up on the energy
        fetch, leaving a partial write pending in the shared connection."""
        def get_json(self, url):
            if "/logical/" in url:
                return self.tree
            if "/by-inverter" in url:
                raise RuntimeError("network down mid-site")
            return {}

    failing_site, good_site = 99, 42
    results = collector.collect(
        conn=conn, site_ids=[failing_site, good_site],
        days=["2026-07-22"], now="NOW",
        bs_for={failing_site: PartialFail(), good_site: FakeBS()})

    ok = {r["site_id"]: r for r in results}
    assert "error" in ok[failing_site]
    # the failing site's partial inventory write must be rolled back
    assert store.load_inventory(conn, failing_site) == []
    # the good site's inventory + energy must still be committed
    assert len(store.load_inventory(conn, good_site)) == 1
    assert len(store.load_energy(conn, good_site, "2026-07-22")) == 1
