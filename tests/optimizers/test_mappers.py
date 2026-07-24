from solaranalysis.optimizers.mappers import flatten_inventory, OptimizerInfo


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
