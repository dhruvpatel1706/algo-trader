"""CryptoAgent tests."""

from __future__ import annotations

from src.agents.base import AssetClass
from src.agents.crypto_agent import CryptoAgent
from src.data.universe import Universe
from src.strategies.failed_breakout_crypto import FailedBreakoutCrypto
from src.strategies.ma_pullback_trend_crypto import MaPullbackTrendCrypto


def test_crypto_agent_default_universe_non_empty():
    Universe.reload()
    a = CryptoAgent()
    expected = Universe.named("crypto_majors")
    assert a.universe == expected
    assert len(a.universe) > 0


def test_crypto_agent_wires_two_default_strategies():
    a = CryptoAgent()
    assert len(a.strategies) == 2
    types = {type(s) for s in a.strategies}
    assert types == {FailedBreakoutCrypto, MaPullbackTrendCrypto}


def test_crypto_agent_evaluate_with_no_bars_returns_empty():
    a = CryptoAgent()
    out = a.evaluate({})
    assert out == []
    assert a._last_eval_ts is not None


def test_crypto_agent_class_metadata():
    assert CryptoAgent.name == "crypto_agent"
    assert CryptoAgent.asset_class is AssetClass.CRYPTO
