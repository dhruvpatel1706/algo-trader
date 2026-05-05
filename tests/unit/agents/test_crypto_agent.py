"""CryptoAgent tests."""

from __future__ import annotations

from src.agents.base import AssetClass
from src.agents.crypto_agent import CryptoAgent
from src.data.universe import Universe


def test_crypto_agent_default_universe_non_empty():
    Universe.reload()
    a = CryptoAgent()
    expected = Universe.named("crypto_majors")
    assert a.universe == expected
    assert len(a.universe) > 0


def test_crypto_agent_starts_with_empty_strategy_list():
    a = CryptoAgent()
    assert a.strategies == []


def test_crypto_agent_evaluate_returns_empty_with_no_strategies():
    a = CryptoAgent()
    out = a.evaluate({})
    assert out == []
    assert a._last_eval_ts is not None


def test_crypto_agent_class_metadata():
    assert CryptoAgent.name == "crypto_agent"
    assert CryptoAgent.asset_class is AssetClass.CRYPTO
