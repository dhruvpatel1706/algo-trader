"""CryptoAgent tests."""

from __future__ import annotations

from src.agents.base import AssetClass
from src.agents.crypto_agent import CryptoAgent
from src.data.universe import Universe
from src.strategies.ema_ribbon_compression import EmaRibbonCompression
from src.strategies.failed_breakout_crypto import FailedBreakoutCrypto
from src.strategies.funding_rate_divergence import FundingRateDivergence
from src.strategies.ma_pullback_trend_crypto import MaPullbackTrendCrypto


def test_crypto_agent_default_universe_non_empty():
    Universe.reload()
    a = CryptoAgent()
    expected = Universe.named("crypto_majors")
    assert a.universe == expected
    assert len(a.universe) > 0


def test_crypto_agent_default_strategies_includes_all_long_only_crypto_paths():
    """The default strategy roster is the complete long-only crypto deck.
    Pinned by type (not count) so future additions don't silently drop a
    strategy. Researcher session 2026-05-07 added EMA Ribbon and Funding
    Rate Divergence to the original two."""
    a = CryptoAgent()
    types = {type(s) for s in a.strategies}
    assert types == {
        FailedBreakoutCrypto,
        MaPullbackTrendCrypto,
        EmaRibbonCompression,
        FundingRateDivergence,
    }


def test_crypto_agent_evaluate_with_no_bars_returns_empty():
    a = CryptoAgent()
    out = a.evaluate({})
    assert out == []
    assert a._last_eval_ts is not None


def test_crypto_agent_class_metadata():
    assert CryptoAgent.name == "crypto_agent"
    assert CryptoAgent.asset_class is AssetClass.CRYPTO
