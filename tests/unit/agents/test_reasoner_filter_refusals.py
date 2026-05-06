"""Refusal-event integration tests for `apply_reasoner_to_signals`.

These extend the existing `test_reasoner_filter.py` suite with the new
``journal_writer`` kwarg behavior. Pinned properties:

  - When the reasoner halts, a ``reasoner_halt`` refusal is emitted.
  - When the reasoner returns ``multiplier < DAMPENED_REFUSAL_THRESHOLD``,
    a ``reasoner_dampened`` refusal is emitted (signal still flows through).
  - At/above the threshold, NO refusal is emitted.
  - When the context builder raises, a ``context_builder_failed`` refusal
    is emitted.
  - Without ``journal_writer``, behavior is byte-identical to the old API
    (no refusals, signal counts unchanged).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from src.agents.autonomous_reasoner import SignalContext, SignalJudgment
from src.agents.reasoner_filter import (
    DAMPENED_REFUSAL_THRESHOLD,
    apply_reasoner_to_signals,
)
from src.strategies.base import Signal


def _signal(symbol: str = "SPY", confidence: float = 0.7) -> Signal:
    return Signal(
        symbol=symbol,
        side="buy",
        entry=Decimal("100"),
        stop=Decimal("95"),
        target=Decimal("110"),
        confidence=confidence,
        strategy_tag="failed_breakout",
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
    """Returns a predictable judgment per call."""

    judgments: list[SignalJudgment]
    calls: list[SignalContext]

    def __init__(self, judgments: list[SignalJudgment]) -> None:
        self.judgments = judgments
        self.calls = []

    def evaluate(self, ctx: SignalContext) -> SignalJudgment:
        self.calls.append(ctx)
        return self.judgments.pop(0)


def _judgment(
    multiplier: float = 1.0, halt: bool = False, reason: str = "ok"
) -> SignalJudgment:
    return SignalJudgment(
        multiplier=multiplier,
        halt=halt,
        reasoning=reason,
        provider="test",
        elapsed_ms=1,
        asof="2026-05-06T00:00:00+00:00",
    )


class _StubWriter:
    """Captures every event passed to `write`."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def write(self, event: dict) -> None:
        self.events.append(event)


# ---------------------------------------------------------------------------
# Halt → reasoner_halt refusal
# ---------------------------------------------------------------------------


def test_halted_signal_emits_reasoner_halt_refusal() -> None:
    sig = _signal()
    fake = _FakeReasoner([_judgment(halt=True, reason="regime mismatch — high VIX")])
    writer = _StubWriter()
    out, _ = apply_reasoner_to_signals(
        [sig], fake, _ctx_builder, journal_writer=writer, agent_name="equity_agent"
    )
    # Signal is dropped (existing behavior).
    assert out == []
    # Exactly one refusal event.
    refusals = [e for e in writer.events if e.get("event") == "refusal"]
    assert len(refusals) == 1
    r = refusals[0]
    assert r["reason"] == "reasoner_halt"
    assert r["symbol"] == "SPY"
    assert r["side"] == "buy"
    assert r["strategy"] == "failed_breakout"
    assert r["agent"] == "equity_agent"
    assert "regime mismatch" in r["detail"]
    # Multiplier from the judgment is preserved in extra.
    assert "multiplier" in r["extra"]
    assert r["extra"]["provider"] == "test"


def test_halt_with_no_journal_writer_emits_nothing() -> None:
    """Backward-compat: if no writer, no refusals."""
    sig = _signal()
    fake = _FakeReasoner([_judgment(halt=True)])
    out, judgments = apply_reasoner_to_signals([sig], fake, _ctx_builder)
    assert out == []
    assert len(judgments) == 1


# ---------------------------------------------------------------------------
# Dampening → reasoner_dampened refusal
# ---------------------------------------------------------------------------


def test_low_multiplier_emits_reasoner_dampened_refusal() -> None:
    sig = _signal(confidence=0.8)
    fake = _FakeReasoner([_judgment(multiplier=0.6, reason="mixed news cluster")])
    writer = _StubWriter()
    out, _ = apply_reasoner_to_signals(
        [sig], fake, _ctx_builder, journal_writer=writer, agent_name="equity_agent"
    )
    # Signal still flows — dampening is observability, not a block.
    assert len(out) == 1
    assert out[0].symbol == "SPY"
    refusals = [e for e in writer.events if e.get("event") == "refusal"]
    assert len(refusals) == 1
    r = refusals[0]
    assert r["reason"] == "reasoner_dampened"
    assert r["symbol"] == "SPY"
    assert r["agent"] == "equity_agent"
    assert "0.6" in r["detail"]
    assert r["extra"]["multiplier"] == 0.6
    assert r["extra"]["threshold"] == DAMPENED_REFUSAL_THRESHOLD


def test_high_multiplier_emits_no_refusal() -> None:
    """multiplier=0.9 is above the dampening threshold — no refusal."""
    sig = _signal(confidence=0.8)
    fake = _FakeReasoner([_judgment(multiplier=0.9)])
    writer = _StubWriter()
    out, _ = apply_reasoner_to_signals(
        [sig], fake, _ctx_builder, journal_writer=writer
    )
    assert len(out) == 1
    refusals = [e for e in writer.events if e.get("event") == "refusal"]
    assert refusals == []


def test_multiplier_at_threshold_emits_no_refusal() -> None:
    """Exactly at threshold (0.7) → not below → no refusal."""
    sig = _signal(confidence=0.8)
    fake = _FakeReasoner([_judgment(multiplier=DAMPENED_REFUSAL_THRESHOLD)])
    writer = _StubWriter()
    out, _ = apply_reasoner_to_signals(
        [sig], fake, _ctx_builder, journal_writer=writer
    )
    assert len(out) == 1
    refusals = [e for e in writer.events if e.get("event") == "refusal"]
    assert refusals == []


def test_multiplier_just_below_threshold_emits_refusal() -> None:
    """Boundary check just below the threshold."""
    sig = _signal(confidence=0.8)
    fake = _FakeReasoner([_judgment(multiplier=DAMPENED_REFUSAL_THRESHOLD - 0.001)])
    writer = _StubWriter()
    out, _ = apply_reasoner_to_signals(
        [sig], fake, _ctx_builder, journal_writer=writer
    )
    assert len(out) == 1
    refusals = [e for e in writer.events if e.get("event") == "refusal"]
    assert len(refusals) == 1
    assert refusals[0]["reason"] == "reasoner_dampened"


def test_dampening_with_no_journal_writer_emits_nothing() -> None:
    sig = _signal(confidence=0.8)
    fake = _FakeReasoner([_judgment(multiplier=0.5)])
    out, _ = apply_reasoner_to_signals([sig], fake, _ctx_builder)
    assert len(out) == 1


# ---------------------------------------------------------------------------
# Context builder failure → context_builder_failed refusal
# ---------------------------------------------------------------------------


def test_context_builder_failure_emits_refusal() -> None:
    sig = _signal()

    def boom(s: Signal) -> SignalContext:
        raise RuntimeError("regime feed offline")

    fake = _FakeReasoner([])  # never called
    writer = _StubWriter()
    out, judgments = apply_reasoner_to_signals(
        [sig], fake, boom, journal_writer=writer, agent_name="equity_agent"
    )
    # Signal passes through (fail-open, existing behavior).
    assert len(out) == 1
    assert out[0].confidence == sig.confidence
    assert len(judgments) == 1
    assert judgments[0].fail_open is True
    refusals = [e for e in writer.events if e.get("event") == "refusal"]
    assert len(refusals) == 1
    r = refusals[0]
    assert r["reason"] == "context_builder_failed"
    assert r["symbol"] == "SPY"
    assert r["agent"] == "equity_agent"
    assert "regime feed offline" in r["detail"]
    assert r["extra"]["exception_type"] == "RuntimeError"


def test_context_builder_failure_without_writer_is_silent() -> None:
    """No writer, no refusals — preserves existing behavior."""
    sig = _signal()

    def boom(s: Signal) -> SignalContext:
        raise RuntimeError("oops")

    fake = _FakeReasoner([])
    out, judgments = apply_reasoner_to_signals([sig], fake, boom)
    assert len(out) == 1
    assert len(judgments) == 1


# ---------------------------------------------------------------------------
# Multiple signals — refusals are emitted in order
# ---------------------------------------------------------------------------


def test_multiple_signals_emit_refusals_in_order() -> None:
    sigs = [_signal("SPY", 0.7), _signal("QQQ", 0.6), _signal("IWM", 0.8)]
    fake = _FakeReasoner(
        [
            _judgment(multiplier=1.0, reason="ok"),       # SPY → no refusal
            _judgment(halt=True, reason="bad regime"),    # QQQ → halt refusal
            _judgment(multiplier=0.55, reason="weak"),    # IWM → dampened refusal
        ]
    )
    writer = _StubWriter()
    out, _ = apply_reasoner_to_signals(
        sigs, fake, _ctx_builder, journal_writer=writer, agent_name="equity_agent"
    )
    # SPY and IWM survive; QQQ halted.
    assert [s.symbol for s in out] == ["SPY", "IWM"]
    refusals = [e for e in writer.events if e.get("event") == "refusal"]
    assert len(refusals) == 2
    assert refusals[0]["symbol"] == "QQQ"
    assert refusals[0]["reason"] == "reasoner_halt"
    assert refusals[1]["symbol"] == "IWM"
    assert refusals[1]["reason"] == "reasoner_dampened"


# ---------------------------------------------------------------------------
# Broken journal must not crash the filter
# ---------------------------------------------------------------------------


def test_broken_journal_writer_does_not_crash_filter() -> None:
    class _BrokenWriter:
        def write(self, event: dict) -> None:
            raise OSError("disk full")

    sig = _signal()
    fake = _FakeReasoner([_judgment(halt=True)])
    out, judgments = apply_reasoner_to_signals(
        [sig], fake, _ctx_builder, journal_writer=_BrokenWriter()
    )
    # Halted as before; no exception escaped.
    assert out == []
    assert len(judgments) == 1
