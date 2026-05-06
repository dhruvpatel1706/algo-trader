"""Tests for the reasoner_filter post-processing glue."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from src.agents.autonomous_reasoner import SignalContext, SignalJudgment
from src.agents.reasoner_filter import apply_reasoner_to_signals
from src.strategies.base import Signal


def _signal(symbol: str = "SPY", confidence: float = 0.7) -> Signal:
    return Signal(
        symbol=symbol,
        side="buy",
        entry=Decimal("100"),
        stop=Decimal("95"),
        target=Decimal("110"),
        confidence=confidence,
        strategy_tag="test",
        timestamp=datetime.now(UTC),
    )


def _ctx_builder(sig: Signal) -> SignalContext:
    return SignalContext(
        symbol=sig.symbol,
        side=sig.side,
        strategy=sig.strategy_tag,
        rule_confidence=sig.confidence,
        entry_price=float(sig.entry),
        stop_price=float(sig.stop),
        target_price=float(sig.target) if sig.target else None,
    )


@dataclass
class _FakeReasoner:
    """Returns a predictable judgment per call. Records each evaluate() arg."""

    judgments: list[SignalJudgment]
    calls: list[SignalContext]

    def __init__(self, judgments: list[SignalJudgment]) -> None:
        self.judgments = judgments
        self.calls = []

    def evaluate(self, ctx: SignalContext) -> SignalJudgment:
        self.calls.append(ctx)
        return self.judgments.pop(0)


def _judgment(multiplier: float = 1.0, halt: bool = False, reason: str = "ok") -> SignalJudgment:
    return SignalJudgment(
        multiplier=multiplier,
        halt=halt,
        reasoning=reason,
        provider="test",
        elapsed_ms=1,
        asof="2026-05-06T00:00:00+00:00",
    )


def test_filter_dampens_confidence_via_multiplier():
    sig = _signal(confidence=0.8)
    fake = _FakeReasoner([_judgment(multiplier=0.5)])
    out, judgments = apply_reasoner_to_signals([sig], fake, _ctx_builder)
    assert len(out) == 1
    assert out[0].confidence == pytest.approx(0.4)
    assert len(judgments) == 1
    assert judgments[0].multiplier == 0.5


def test_filter_halts_signal_when_judgment_halts():
    sig = _signal()
    fake = _FakeReasoner([_judgment(halt=True, reason="regime mismatch")])
    out, judgments = apply_reasoner_to_signals([sig], fake, _ctx_builder)
    assert out == []
    # Halted judgment is STILL in the audit list.
    assert len(judgments) == 1
    assert judgments[0].halt is True


def test_filter_clamps_confidence_to_unit_interval():
    """Even if multiplier=1.2 and rule_confidence=0.95, output ≤ 1.0."""
    sig = _signal(confidence=0.95)
    fake = _FakeReasoner([_judgment(multiplier=1.2)])
    out, _ = apply_reasoner_to_signals([sig], fake, _ctx_builder)
    assert out[0].confidence <= 1.0


def test_filter_passes_through_when_context_builder_raises():
    """Bad context builder must not stop the signal — fail-open behavior."""
    sig = _signal()

    def boom(s: Signal) -> SignalContext:
        raise RuntimeError("regime feed offline")

    fake = _FakeReasoner([])  # never called
    out, judgments = apply_reasoner_to_signals([sig], fake, boom)
    assert len(out) == 1
    assert out[0].confidence == sig.confidence  # unchanged
    assert len(judgments) == 1
    assert judgments[0].fail_open is True
    assert "context build failed" in judgments[0].reasoning


def test_filter_processes_multiple_signals_in_order():
    sigs = [_signal("SPY", 0.7), _signal("QQQ", 0.6), _signal("IWM", 0.8)]
    fake = _FakeReasoner(
        [
            _judgment(multiplier=1.1),    # SPY
            _judgment(halt=True),          # QQQ halted
            _judgment(multiplier=0.9),     # IWM
        ]
    )
    out, judgments = apply_reasoner_to_signals(sigs, fake, _ctx_builder)
    assert [s.symbol for s in out] == ["SPY", "IWM"]
    assert len(judgments) == 3
    # Confidence math
    assert out[0].confidence == pytest.approx(0.7 * 1.1)
    assert out[1].confidence == pytest.approx(0.8 * 0.9)


def test_filter_empty_input_returns_empty():
    fake = _FakeReasoner([])
    out, judgments = apply_reasoner_to_signals([], fake, _ctx_builder)
    assert out == []
    assert judgments == []


def test_reasoner_called_with_correct_context():
    sig = _signal("NVDA", 0.6)
    fake = _FakeReasoner([_judgment(multiplier=1.0)])
    apply_reasoner_to_signals([sig], fake, _ctx_builder)
    assert len(fake.calls) == 1
    ctx = fake.calls[0]
    assert ctx.symbol == "NVDA"
    assert ctx.side == "buy"
    assert ctx.rule_confidence == 0.6
    assert ctx.entry_price == 100.0
