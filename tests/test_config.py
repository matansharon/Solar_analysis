import os
from solaranalysis.config import load_config

def test_load_config_resolves_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SE_USER", "alice@example.com")
    monkeypatch.setenv("SE_PASS", "secret")
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        "model: null\n"
        "max_input_tokens: 1000\n"
        "output_language: en\n"
        "plants:\n"
        "  - name: Roof\n"
        "    auth:\n"
        "      platform: solaredge\n"
        "      mode: password\n"
        "      username: ${SE_USER}\n"
        "      password: ${SE_PASS}\n"
        "    tariff_per_kwh: 0.5\n"
        "    currency: ILS\n",
        encoding="utf-8",
    )
    cfg = load_config(str(cfg_file))
    assert len(cfg.plants) == 1
    p = cfg.plants[0]
    assert p.name == "Roof"
    assert p.auth.platform == "solaredge"
    assert p.auth.username == "alice@example.com"
    assert p.auth.password == "secret"
    assert p.tariff_per_kwh == 0.5
    assert cfg.max_input_tokens == 1000

def test_missing_env_raises(tmp_path):
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        "plants:\n  - name: X\n    auth:\n      platform: growatt\n"
        "      mode: password\n      username: ${NOPE_MISSING}\n      password: x\n",
        encoding="utf-8",
    )
    import pytest
    with pytest.raises(ValueError, match="NOPE_MISSING"):
        load_config(str(cfg_file))
