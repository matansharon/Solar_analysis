from solaranalysis.optimizers.mappers import flatten_inventory, OptimizerInfo, map_by_inverter_energy, OptimizerEnergyRow


def _energy_payload():
    return {"siteId": 1, "startDate": "2026-07-22", "endDate": "2026-07-22",
            "inverters": [
                {"serial": "INV-1", "energy": {"value": 100.0, "unit": "watt-hour"},
                 "optimizers": [
                     {"serial": "OPT-A", "energy": {"value": 5824.75, "unit": "watt-hour"},
                      "temperature": {"temperature": None, "temperatureUnit": None},
                      "color": 0.978},
                     {"serial": "OPT-B", "energy": {"value": 0.0, "unit": "watt-hour"},
                      "temperature": {"temperature": 41.5, "temperatureUnit": "C"},
                      "color": 0.0}]},
                {"serial": "INV-2", "optimizers": [
                     {"serial": "OPT-C", "energy": {"value": 4000.0, "unit": "watt-hour"},
                      "color": 0.71}]}]}


def test_map_by_inverter_energy_flattens_all_optimizers():
    rows = map_by_inverter_energy(_energy_payload())
    assert len(rows) == 3
    by = {r.optimizer_serial: r for r in rows}
    assert isinstance(by["OPT-A"], OptimizerEnergyRow)
    assert by["OPT-A"].inverter_serial == "INV-1"
    assert by["OPT-A"].energy_wh == 5824.75
    assert by["OPT-A"].color == 0.978
    assert by["OPT-A"].temperature_c is None
    assert by["OPT-B"].energy_wh == 0.0 and by["OPT-B"].temperature_c == 41.5
    assert by["OPT-C"].inverter_serial == "INV-2" and by["OPT-C"].energy_wh == 4000.0


def test_map_by_inverter_energy_tolerates_missing_pieces():
    rows = map_by_inverter_energy({"inverters": [
        {"serial": "INV-1", "optimizers": [
            {"serial": "OPT-X"},  # no energy/color/temperature
            {"energy": {"value": 1.0}}]}]})  # no serial -> skipped
    assert len(rows) == 1
    assert rows[0].optimizer_serial == "OPT-X"
    assert rows[0].energy_wh is None and rows[0].color is None and rows[0].temperature_c is None


def test_map_by_inverter_energy_empty():
    assert map_by_inverter_energy({}) == []


def _tree():
    # Minimal synthetic siteStructure mirroring the live shape:
    # SITE -> FOLDER(INVERTER) -> INVERTER -> FOLDER(STRING) -> STRING
    #      -> FOLDER(OPTIMIZER) -> OPTIMIZER leaves.
    opt = lambda s, d: {"type": "OPTIMIZER", "serial": s, "name": f"Optimizer {d}",
                        "displayOrder": d,
                        "properties": {"model": "P950-4RM4MBY-NM24", "status": "ACTIVE"}}
    return {"siteStructure": {
        "type": "SITE", "name": "Site",
        "children": [{"type": "FOLDER", "name": "INVERTER", "children": [
            {"type": "INVERTER", "serial": "INV-1", "name": "Inverter 1",
             "properties": {"model": "SE50K-IL00IBNQ4", "status": "ACTIVE"},
             "children": [{"type": "FOLDER", "name": "STRING", "children": [
                 {"type": "STRING", "name": "String 1.0", "displayOrder": "1.0",
                  "children": [{"type": "FOLDER", "name": "OPTIMIZER", "children": [
                      opt("136F487F-49", "1.0.1"), opt("136BCBF7-40", "1.0.2")]}]}]}]}]}]}}


def test_flatten_inventory_extracts_optimizers_with_lineage():
    infos = flatten_inventory(_tree())
    assert len(infos) == 2
    a = infos[0]
    assert isinstance(a, OptimizerInfo)
    assert a.serial == "136F487F-49"
    assert a.label == "1.0.1"
    assert a.name == "Optimizer 1.0.1"
    assert a.inverter_serial == "INV-1"
    assert a.inverter_name == "Inverter 1"
    assert a.string_label == "1.0"
    assert a.string_name == "String 1.0"
    assert a.model == "P950-4RM4MBY-NM24"
    assert a.status == "ACTIVE"


def test_flatten_inventory_accepts_bare_node_and_ignores_non_optimizers():
    infos = flatten_inventory(_tree()["siteStructure"])  # bare node, no wrapper
    assert [i.serial for i in infos] == ["136F487F-49", "136BCBF7-40"]


def test_flatten_inventory_empty_payload():
    assert flatten_inventory({}) == []
    assert flatten_inventory({"siteStructure": {}}) == []
