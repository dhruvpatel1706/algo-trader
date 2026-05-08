"""CryptoAgent tests."""

from __future__ import annotations

from src.agents.base import AssetClass
from src.agents.crypto_agent import CryptoAgent
from src.data.universe import Universe
from src.strategies.base import Signal, Strategy
from src.strategies.ema_ribbon_compression import EmaRibbonCompression
from src.strategies.failed_breakout_crypto import FailedBreakoutCrypto
from src.strategies.funding_rate_divergence import FundingRateDivergence
from src.strategies.ma_pullback_trend_crypto import MaPullbackTrendCrypto


def _empty_loader(*args, **kwargs):
    """Stub bars loader used in tests so the agent's on-demand 4h fetch
    does NOT hit the production crypto loader chain (which 401s on
    Alpaca paper without keys and 451s on Binance from US IPs)."""
    return {}


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
    a = CryptoAgent(bars_loader=_empty_loader)
    out = a.evaluate({})
    assert out == []
    assert a._last_eval_ts is not None


def test_crypto_agent_class_metadata():
    assert CryptoAgent.name == "crypto_agent"
    assert CryptoAgent.asset_class is AssetClass.CRYPTO


# ---------------------------------------------------------------------------
# Multi-timeframe dispatch (H2.2 4h crypto bars cache)
#
# Each strategy declares a ``bar_interval`` class attribute (default
# ``"1d"``). The agent must fetch the right bars per strategy: daily
# bars from the runner-supplied dict, intra-day intervals via the
# injected ``bars_loader``. These tests pin that contract.
# ---------------------------------------------------------------------------


class _DailyStub(Strategy):
    """1d-bar strategy that fires one signal per symbol it sees."""

    name = "daily_stub"
    bar_interval = "1d"

    def universe(self) -> tuple[str, ...]:
        return ("BTCUSDT",)

    def generate_signals(self, bars):
        return [_one_signal(sym, "daily_stub") for sym in bars]


class _FourHourStub(Strategy):
    """4h-bar strategy that fires one signal per symbol it sees."""

    name = "four_hour_stub"
    bar_interval = "4h"

    def universe(self) -> tuple[str, ...]:
        return ("BTCUSDT",)

    def generate_signals(self, bars):
        return [_one_signal(sym, "four_hour_stub") for sym in bars]


def _one_signal(sym: str, tag: str) -> Signal:
    from datetime import UTC, datetime
    from decimal import Decimal

    return Signal(
        symbol=sym,
        side="buy",
        entry=Decimal("100"),
        stop=Decimal("99"),
        target=Decimal("103"),
        confidence=0.5,
        strategy_tag=tag,
        timestamp=datetime.now(UTC),
    )


def test_crypto_agent_passes_runner_bars_to_daily_strategies():
    """Strategies with bar_interval='1d' (the default) must receive the
    bars dict that the runner supplied — no additional fetch."""
    fetch_calls = {"n": 0}

    def loader(*args, **kwargs):
        fetch_calls["n"] += 1
        return {}

    a = CryptoAgent(strategies=[_DailyStub()], bars_loader=loader)
    bars = {"BTCUSDT": "<daily-bars>", "ETHUSDT": "<daily-bars>"}
    sigs = a.evaluate(bars)
    assert len(sigs) == 2
    assert fetch_calls["n"] == 0, "daily strategies must not trigger a fetch"


def test_crypto_agent_fetches_4h_bars_for_4h_strategies():
    """Strategies with bar_interval='4h' trigger an on-demand fetch via
    the injected loader, with interval='4h' kw-passed."""
    fetched_intervals: list[str] = []

    def loader(symbols, start, end, *, interval):
        fetched_intervals.append(interval)
        return {sym: "<4h-bars>" for sym in symbols}

    a = CryptoAgent(strategies=[_FourHourStub()], bars_loader=loader)
    sigs = a.evaluate({})  # empty 1d bars; strategy is 4h
    assert fetched_intervals == ["4h"]
    # The strategy fired against the loader-fetched 4h bars (universe
    # default is crypto_majors, which is 11 symbols — see universes.yaml).
    assert len(sigs) >= 1


def test_crypto_agent_fetches_each_interval_only_once_per_cycle():
    """Two 4h strategies in the same cycle must share ONE fetch."""
    fetch_count = {"n": 0}

    def loader(symbols, start, end, *, interval):
        fetch_count["n"] += 1
        return {sym: "<4h>" for sym in symbols}

    class _Another4h(_FourHourStub):
        name = "another_4h"

    a = CryptoAgent(strategies=[_FourHourStub(), _Another4h()], bars_loader=loader)
    a.evaluate({})
    assert fetch_count["n"] == 1, (
        f"expected 1 fetch shared across 2 4h strategies; got {fetch_count['n']}"
    )


def test_crypto_agent_skips_strategy_when_4h_fetch_returns_empty():
    """Loader returns nothing (e.g. all providers down) -> strategy
    skipped silently rather than passed an empty dict that would mask
    the outage as a "no setup" outcome."""
    a = CryptoAgent(strategies=[_FourHourStub()], bars_loader=_empty_loader)
    sigs = a.evaluate({})
    assert sigs == []


def test_crypto_agent_continues_when_4h_loader_raises():
    """A loader exception must not propagate — degrade to skip-this-cycle."""
    def boom(*args, **kwargs):
        raise RuntimeError("upstream 503")

    a = CryptoAgent(
        strategies=[_DailyStub(), _FourHourStub()], bars_loader=boom
    )
    sigs = a.evaluate({"BTCUSDT": "<daily>"})
    # The daily strategy still produces its signal; the 4h strategy
    # gracefully skips.
    assert len(sigs) == 1
    assert sigs[0].strategy_tag == "daily_stub"
