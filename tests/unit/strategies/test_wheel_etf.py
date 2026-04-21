"""wheel_etf v1 stub tests."""

from __future__ import annotations

from src.strategies import load_strategy
from src.strategies.wheel_etf import WheelEtf, WheelParams


def test_wheel_etf_loads_via_registry():
    s = load_strategy("wheel_etf")
    assert isinstance(s, WheelEtf)
    assert s.universe() == ("SPY", "QQQ")
    assert isinstance(s.params, WheelParams)


def test_wheel_etf_emits_no_signals_in_v1():
    s = WheelEtf()
    sigs = s.generate_signals({"SPY": object(), "QQQ": object()})
    assert sigs == []


def test_wheel_params_defaults_in_documented_ranges():
    p = WheelParams()
    assert -0.40 <= p.target_delta <= -0.20
    assert 30 <= p.dte_min <= 60
    assert 30 <= p.dte_max <= 60
    assert 0.30 <= p.profit_target_pct <= 0.70
    assert 25 <= p.ivr_min <= 50
    assert 14 <= p.dte_roll <= 28
