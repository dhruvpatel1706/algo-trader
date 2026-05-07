"""Analyst agent unit tests.

The analyst is the pre-trade reviewer that wraps TradingView multi-
timeframe ratings + chart context + (optional) LLM synthesis into a
structured verdict. Tests inject a fake rating fetcher so they stay
deterministic and offline.

Coverage:
  - Hard veto on long-into-STRONG_SELL-daily.
  - Boost (multiplier > 1.0) on full bullish alignment.
  - Dampen (multiplier < 1.0) on bearish-leaning timeframes.
  - Graceful degradation when rating fetch fails.
  - LLM path uses LLM verdict; falls back to rules when LLM throws.
  - Routing: crypto USDT pair -> crypto/BINANCE; equity ticker -> america/NASDAQ.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from src.agents.analyst import (
    Analyst,
    AnalystVerdict,
    TimeframeRating,
    _parse_llm_verdict,
)


@dataclass
class _FakeSig:
    symbol: str
    side: str
    entry: Decimal
    stop: Decimal
    target: Decimal | None
    confidence: float
    strategy_tag: str
    asset_class: str | None = None


@dataclass
class _FakeCtx:
    recent_bars: Any = None


def _rating(tf: str, reco: str, buy: int, sell: int, neutral: int) -> TimeframeRating:
    score_map = {
        "STRONG_SELL": -2,
        "SELL": -1,
        "NEUTRAL": 0,
        "BUY": 1,
        "STRONG_BUY": 2,
    }
    return TimeframeRating(
        timeframe=tf,
        recommendation=reco,
        buy=buy,
        sell=sell,
        neutral=neutral,
        score=score_map[reco],
    )


def _make_fetcher(by_tf: dict[str, TimeframeRating]):
    """Return a fake rating_fetcher that serves the dict by timeframe."""
    calls: list[tuple[str, str, str, str]] = []

    def fetch(symbol: str, screener: str, exchange: str, timeframe: str):
        calls.append((symbol, screener, exchange, timeframe))
        return by_tf.get(timeframe)

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


# ---------------------------------------------------------------------------
# Hard veto
# ---------------------------------------------------------------------------


def test_long_into_strong_sell_daily_is_vetoed():
    """The defining safety rule. Long signals must not fire when TV
    daily reads STRONG_SELL — we'd be fading a confirmed downtrend."""
    fetcher = _make_fetcher(
        {"1D": _rating("1D", "STRONG_SELL", 2, 16, 9)}
    )
    a = Analyst(rating_fetcher=fetcher)
    sig = _FakeSig(
        "BTCUSDT", "buy",
        Decimal("80000"), Decimal("78000"), Decimal("85000"),
        0.55, "ema_ribbon_compression", "crypto",
    )
    v = a.evaluate(sig, _FakeCtx())
    assert v.accept is False
    assert v.multiplier == 0.0
    assert v.veto_reason and "STRONG_SELL" in v.veto_reason
    assert v.expected_r < 0


def test_long_into_strong_sell_4h_is_also_vetoed():
    """4H veto rule too. Ensures the rule covers more than just daily."""
    fetcher = _make_fetcher(
        {"4H": _rating("4H", "STRONG_SELL", 1, 15, 9)}
    )
    a = Analyst(rating_fetcher=fetcher)
    sig = _FakeSig(
        "ETHUSDT", "buy",
        Decimal("2300"), Decimal("2200"), Decimal("2500"),
        0.55, "ma_pullback_trend_crypto", "crypto",
    )
    v = a.evaluate(sig, _FakeCtx())
    assert v.accept is False
    assert v.veto_reason and "STRONG_SELL" in v.veto_reason


# ---------------------------------------------------------------------------
# Boost / dampen
# ---------------------------------------------------------------------------


def test_full_bullish_alignment_boosts_multiplier_to_max():
    """Daily + 4H + 1H all STRONG_BUY → multiplier hits the 1.2 cap."""
    fetcher = _make_fetcher({
        "1D": _rating("1D", "STRONG_BUY", 16, 1, 9),
        "4H": _rating("4H", "STRONG_BUY", 18, 0, 8),
        "1H": _rating("1H", "STRONG_BUY", 15, 3, 8),
    })
    a = Analyst(rating_fetcher=fetcher)
    sig = _FakeSig(
        "DDOG", "buy",
        Decimal("250"), Decimal("240"), Decimal("270"),
        0.6, "test_strategy", "equity",
    )
    v = a.evaluate(sig, _FakeCtx())
    assert v.accept is True
    assert v.multiplier == 1.2
    # Win probability should reflect the strong bullish vote share.
    assert v.win_probability >= 0.6
    assert v.expected_r > 0


def test_bearish_leaning_dampens_multiplier_below_one():
    """Daily + 4H + 1H all SELL (not STRONG_SELL) → no veto, but multiplier
    drops to 0.5. Half-size protection without blocking the trade entirely."""
    fetcher = _make_fetcher({
        "1D": _rating("1D", "SELL", 5, 11, 10),
        "4H": _rating("4H", "SELL", 3, 14, 9),
        "1H": _rating("1H", "SELL", 4, 15, 7),
    })
    a = Analyst(rating_fetcher=fetcher)
    sig = _FakeSig(
        "ETHUSDT", "buy",
        Decimal("2300"), Decimal("2200"), Decimal("2500"),
        0.55, "ma_pullback_trend_crypto", "crypto",
    )
    v = a.evaluate(sig, _FakeCtx())
    assert v.accept is True  # no veto trigger
    assert v.multiplier == 0.5  # heavy dampening
    assert v.win_probability < 0.5  # bearish TV → low win prob


def test_neutral_top_tf_uses_mid_multiplier():
    """Daily NEUTRAL with mixed lower TFs → middling multiplier."""
    fetcher = _make_fetcher({
        "1D": _rating("1D", "NEUTRAL", 9, 8, 12),
        "4H": _rating("4H", "BUY", 12, 5, 10),
        "1H": _rating("1H", "NEUTRAL", 8, 8, 12),
    })
    a = Analyst(rating_fetcher=fetcher)
    sig = _FakeSig(
        "AAPL", "buy",
        Decimal("220"), Decimal("215"), Decimal("230"),
        0.55, "ma_pullback_trend", "equity",
    )
    v = a.evaluate(sig, _FakeCtx())
    assert v.accept is True
    # Aligned score: NEUTRAL=0 (50% wt) + BUY=+1 (30% wt) + NEUTRAL=0 (20% wt) = 0.3
    # Falls in [-0.5, 0.5] band → multiplier 0.8.
    assert v.multiplier == 0.8


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


def test_no_tv_data_proceeds_without_blocking():
    """All TV fetches return None → conservative pass-through, no veto.
    Better than blocking trades when TV is unreachable."""

    def fetcher_returning_none(*_args, **_kwargs):
        return None

    a = Analyst(rating_fetcher=fetcher_returning_none)
    sig = _FakeSig(
        "BTCUSDT", "buy",
        Decimal("80000"), Decimal("78000"), Decimal("85000"),
        0.55, "ema_ribbon_compression", "crypto",
    )
    v = a.evaluate(sig, _FakeCtx())
    assert v.accept is True
    assert v.multiplier == 1.0
    assert v.timeframe_ratings == []
    assert "unavailable" in v.reasoning.lower()


def test_fetcher_exceptions_are_caught_per_timeframe():
    """If one timeframe's fetch raises, the other timeframes still
    contribute. Never crash the whole eval."""

    def fetcher_partial(symbol, screener, exchange, timeframe):
        if timeframe == "1H":
            raise RuntimeError("simulated 1H fetcher failure")
        return _rating(timeframe, "BUY", 14, 3, 9)

    a = Analyst(rating_fetcher=fetcher_partial)
    sig = _FakeSig(
        "AAPL", "buy",
        Decimal("220"), Decimal("215"), Decimal("230"),
        0.55, "test_strategy", "equity",
    )
    v = a.evaluate(sig, _FakeCtx())
    assert v.accept is True
    # 1D and 4H succeeded → 2 ratings retained.
    assert len(v.timeframe_ratings) == 2


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def test_crypto_usdt_pair_routes_to_crypto_binance():
    """A USDT-suffixed symbol with no asset_class should still route
    to the crypto screener via the suffix heuristic."""
    fetcher = _make_fetcher({"1D": _rating("1D", "BUY", 12, 5, 8)})
    a = Analyst(rating_fetcher=fetcher)
    sig = _FakeSig(
        "SOLUSDT", "buy",
        Decimal("85"), Decimal("82"), Decimal("95"),
        0.55, "test_strategy", None,  # asset_class missing
    )
    a.evaluate(sig, _FakeCtx())
    assert fetcher.calls[0] == ("SOLUSDT", "crypto", "BINANCE", "1D")  # type: ignore[attr-defined]


def test_equity_ticker_routes_to_america_nasdaq():
    fetcher = _make_fetcher({"1D": _rating("1D", "BUY", 12, 5, 8)})
    a = Analyst(rating_fetcher=fetcher)
    sig = _FakeSig(
        "DDOG", "buy",
        Decimal("250"), Decimal("240"), Decimal("270"),
        0.55, "test_strategy", "equity",
    )
    a.evaluate(sig, _FakeCtx())
    assert fetcher.calls[0][1:3] == ("america", "NASDAQ")  # type: ignore[attr-defined]


def test_screener_override_wins_over_default():
    """Per-symbol override forces a specific screener/exchange. Useful
    for assets only on KuCoin or specific equity exchanges."""
    fetcher = _make_fetcher({"1D": _rating("1D", "BUY", 12, 5, 8)})
    a = Analyst(
        rating_fetcher=fetcher,
        screener_overrides={"BTCUSDT": ("crypto", "KUCOIN")},
    )
    sig = _FakeSig(
        "BTCUSDT", "buy",
        Decimal("80000"), Decimal("78000"), Decimal("85000"),
        0.55, "test_strategy", "crypto",
    )
    a.evaluate(sig, _FakeCtx())
    assert fetcher.calls[0][1:3] == ("crypto", "KUCOIN")  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# LLM path
# ---------------------------------------------------------------------------


def test_llm_verdict_used_when_router_returns_valid_json():
    """When LLM is wired and returns valid JSON, that verdict overrides
    the rule-based one. Rule-baseline is still used as input context."""
    fetcher = _make_fetcher({
        "1D": _rating("1D", "BUY", 12, 5, 8),
        "4H": _rating("4H", "NEUTRAL", 8, 8, 12),
    })

    class FakeLLM:
        def call(self, *, system, user, max_tokens=500):
            class R:
                text = (
                    '{"accept": true, "multiplier": 0.9, "win_probability": 0.6, '
                    '"expected_r": 0.4, "reasoning": "MTF mostly bullish but lower '
                    'TFs neutral; size cautiously.", "veto_reason": null, '
                    '"catalyst": null}'
                )
            return R()

    a = Analyst(rating_fetcher=fetcher, llm_router=FakeLLM())
    sig = _FakeSig(
        "AAPL", "buy",
        Decimal("220"), Decimal("215"), Decimal("230"),
        0.55, "test_strategy", "equity",
    )
    v = a.evaluate(sig, _FakeCtx())
    assert v.source == "llm"
    assert v.multiplier == 0.9
    assert "size cautiously" in v.reasoning


def test_llm_failure_falls_back_to_rules():
    """LLM raises → rule verdict used, source labelled 'rules-llm-failed'
    so the operator knows the LLM didn't contribute."""
    fetcher = _make_fetcher({"1D": _rating("1D", "BUY", 12, 5, 8)})

    class BrokenLLM:
        def call(self, *, system, user, max_tokens=500):
            raise RuntimeError("simulated LLM outage")

    a = Analyst(rating_fetcher=fetcher, llm_router=BrokenLLM())
    sig = _FakeSig(
        "AAPL", "buy",
        Decimal("220"), Decimal("215"), Decimal("230"),
        0.55, "test_strategy", "equity",
    )
    v = a.evaluate(sig, _FakeCtx())
    assert v.source == "rules-llm-failed"
    assert v.accept is True


def test_llm_clamps_hallucinated_multiplier():
    """If the LLM returns multiplier=2.5 (above the 1.2 cap), the
    analyst clamps it. Prevents oversizing on hallucinated outputs."""
    fetcher = _make_fetcher({"1D": _rating("1D", "BUY", 12, 5, 8)})

    class WildLLM:
        def call(self, *, system, user, max_tokens=500):
            class R:
                text = (
                    '{"accept": true, "multiplier": 2.5, "win_probability": 0.99, '
                    '"expected_r": 50, "reasoning": "moon"}'
                )
            return R()

    a = Analyst(rating_fetcher=fetcher, llm_router=WildLLM())
    sig = _FakeSig(
        "BTCUSDT", "buy",
        Decimal("80000"), Decimal("78000"), Decimal("85000"),
        0.55, "test_strategy", "crypto",
    )
    v = a.evaluate(sig, _FakeCtx())
    assert v.multiplier == 1.2  # clamped
    assert v.win_probability == 0.95  # clamped


# ---------------------------------------------------------------------------
# JSON parser tolerance
# ---------------------------------------------------------------------------


def test_parser_handles_code_fenced_response():
    text = '```json\n{"accept": true, "multiplier": 1.0}\n```'
    out = _parse_llm_verdict(text)
    assert out == {"accept": True, "multiplier": 1.0}


def test_parser_handles_prose_with_embedded_json():
    text = (
        "Here's my analysis:\n\n"
        '{"accept": false, "multiplier": 0.0}\n\n'
        "(end of analysis)"
    )
    out = _parse_llm_verdict(text)
    assert out == {"accept": False, "multiplier": 0.0}


def test_parser_returns_none_on_invalid_json():
    assert _parse_llm_verdict("not json at all") is None


# ---------------------------------------------------------------------------
# Verdict serialisation (dashboard / journal)
# ---------------------------------------------------------------------------


def test_verdict_to_dict_is_json_safe():
    """The journal serialises verdicts as JSON; ensure to_dict() output
    has no unserialisable types (e.g. dataclass instances)."""
    fetcher = _make_fetcher({"1D": _rating("1D", "BUY", 12, 5, 8)})
    a = Analyst(rating_fetcher=fetcher)
    sig = _FakeSig(
        "AAPL", "buy",
        Decimal("220"), Decimal("215"), Decimal("230"),
        0.55, "test_strategy", "equity",
    )
    v = a.evaluate(sig, _FakeCtx())
    d = v.to_dict()
    import json
    serialised = json.dumps(d)
    assert serialised  # round-trips cleanly
    assert "timeframe_ratings" in d
    assert isinstance(d["timeframe_ratings"], list)


def test_analyst_verdict_constructible_directly():
    """Pin the public dataclass shape — code outside the module
    constructs AnalystVerdict in tests/mocks."""
    v = AnalystVerdict(
        accept=True,
        multiplier=1.0,
        win_probability=0.5,
        expected_r=0.0,
        reasoning="test",
        veto_reason=None,
    )
    assert v.accept is True
    assert v.timeframe_ratings == []
