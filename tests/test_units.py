import math
from solaranalysis.core import units

def test_to_float_basic():
    assert units.to_float("3.5") == 3.5
    assert units.to_float(42) == 42.0
    assert units.to_float(None) is None
    assert units.to_float("") is None
    assert units.to_float("   ") is None
    assert units.to_float("abc") is None

def test_to_float_none_tokens_and_commas():
    assert units.to_float("No Data", none_tokens=("no data",)) is None
    assert units.to_float("1,234.5", strip_commas=True) == 1234.5
    # without strip_commas a comma is not silently dropped
    assert units.to_float("1,234") is None

def test_w_to_kw():
    assert units.w_to_kw(3200) == 3.2
    assert units.w_to_kw(None) is None

def test_wh_to_kwh():
    assert units.wh_to_kwh(1500) == 1.5
    assert units.wh_to_kwh(None) is None

def test_specific_yield():
    assert units.specific_yield(1200.0, 100.0) == 12.0
    assert units.specific_yield(1200.0, 0) is None
    assert units.specific_yield(None, 100.0) is None

def test_capacity_factor():
    cf = units.capacity_factor(240.0, 100.0, 24.0)
    assert math.isclose(cf, 0.1)
    assert units.capacity_factor(1.0, 0, 24.0) is None

def test_money():
    assert math.isclose(units.money(100.0, 0.55), 55.0)
    assert units.money(100.0, None) is None

def test_round_opt():
    assert units.round_opt(1.23456) == 1.23
    assert units.round_opt(None) is None
