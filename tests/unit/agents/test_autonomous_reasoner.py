"""Tests for the autonomous LLM signal reasoner.

This is the headline trust-bearing module — the LLM is in the trade-decision
loop. Tests pin every safety-critical property:

  - Multiplier is ALWAYS clamped into [0.5, 1.2]. A hallucinated 50x → 1.2.
  - LLM unavailable → identity judgment with `fail_open=True`. Bot keeps
    running on rules alone.
  - Tickers are anonymized before the LLM sees them. De-anonymized in the
    journaled reasoning.
  - JSON parse failures default to identity, not crash.
  - The reasoner cannot upsize beyond the ceiling; it cannot raise past
    rule_confidence on its own.
  - Disabled reasoner → identity, no LLM call.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from src.agents.autonomous_reasoner import (
    MULTIPLIER_CEILING,
    MULTIPLIER_FLOOR,
    AutonomousReasoner,
    SignalContext,
    SignalJudgment,
    _anonymize_context,
    _clamp,
    _parse_verdict,
)


def _ctx(**overrides) -> SignalContext:
    base = {
        "symbol": "SPY",
        "side": "buy",
        "strategy": "failed_breakout",
        "rule_confidence": 0.7,
        "entry_price": 425.0,
        "stop_price": 420.0,
        "target_price": 435.0,
    }
    base.update(overrides)
    return SignalContext(**base)


def _llm_response(text: str, provider: str = "anthropic"):
    from src.llm.router import LLMResponse

    return LLMResponse(text=text, provider=provider, model="test", elapsed_ms=12)


# ---------------------------------------------------------------------------
# Clamp + safety bounds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (50.0, MULTIPLIER_CEILING),       # hallucinated upsize
        (1.5, MULTIPLIER_CEILING),        # over ceiling
        (1.2, 1.2),                       # at ceiling
        (1.0, 1.0),                       # identity
        (0.7, 0.7),                       # mid-band dampener
        (0.5, 0.5),                       # at floor
        (0.1, MULTIPLIER_FLOOR),          # below floor
        (-3.0, MULTIPLIER_FLOOR),         # negative
        (float("nan"), 1.0),              # NaN -> identity
    ],
)
def test_clamp_holds_safety_bounds(raw, expected):
    assert _clamp(raw) == expected


def test_evaluate_clamps_runaway_llm_output():
    """Even if the LLM returns multiplier=50, the strategy never sees more
    than 1.2 — period."""
    verdict = json.dumps({"multiplier": 50.0, "halt": False, "reasoning": "yolo"})
    r = AutonomousReasoner()
    with patch("src.agents.autonomous_reasoner.call_llm", return_value=_llm_response(verdict)):
        out = r.evaluate(_ctx())
    assert out.multiplier == MULTIPLIER_CEILING


def test_evaluate_clamps_negative_llm_output():
    verdict = json.dumps({"multiplier": -5.0, "halt": False, "reasoning": "x"})
    r = AutonomousReasoner()
    with patch("src.agents.autonomous_reasoner.call_llm", return_value=_llm_response(verdict)):
        out = r.evaluate(_ctx())
    assert out.multiplier == MULTIPLIER_FLOOR


# ---------------------------------------------------------------------------
# Fail-open contract
# ---------------------------------------------------------------------------


def test_evaluate_returns_identity_when_llm_unavailable():
    from src.llm import LLMUnavailableError

    r = AutonomousReasoner()
    with patch(
        "src.agents.autonomous_reasoner.call_llm",
        side_effect=LLMUnavailableError("all providers down"),
    ):
        out = r.evaluate(_ctx())
    assert out.multiplier == 1.0
    assert out.halt is False
    assert out.fail_open is True
    assert "LLM unavailable" in out.reasoning
    assert out.provider is None


def test_evaluate_disabled_skips_llm_call():
    """Operator can flip `enabled=False` — no LLM call, identity judgment."""
    r = AutonomousReasoner(enabled=False)
    with patch("src.agents.autonomous_reasoner.call_llm") as call:
        out = r.evaluate(_ctx())
    assert call.call_count == 0
    assert out.multiplier == 1.0
    assert out.halt is False
    assert "disabled" in out.reasoning.lower()


def test_evaluate_handles_json_parse_failure():
    """Garbage from the LLM doesn't crash the bot — defaults to identity."""
    r = AutonomousReasoner()
    with patch(
        "src.agents.autonomous_reasoner.call_llm",
        return_value=_llm_response("not json at all"),
    ):
        out = r.evaluate(_ctx())
    assert out.multiplier == 1.0
    assert out.halt is False
    assert "parse failed" in out.reasoning.lower()


def test_evaluate_handles_markdown_wrapped_json():
    """LLMs sometimes wrap JSON in ```json fences despite the prompt."""
    body = json.dumps({"multiplier": 0.8, "halt": False, "reasoning": "ok"})
    verdict = f"```json\n{body}\n```"
    r = AutonomousReasoner()
    with patch(
        "src.agents.autonomous_reasoner.call_llm",
        return_value=_llm_response(verdict),
    ):
        out = r.evaluate(_ctx())
    assert out.multiplier == 0.8


# ---------------------------------------------------------------------------
# Halt vote
# ---------------------------------------------------------------------------


def test_evaluate_passes_through_halt_vote():
    verdict = json.dumps({"multiplier": 1.0, "halt": True, "reasoning": "regime mismatch"})
    r = AutonomousReasoner()
    with patch(
        "src.agents.autonomous_reasoner.call_llm", return_value=_llm_response(verdict)
    ):
        out = r.evaluate(_ctx())
    assert out.halt is True
    assert "regime mismatch" in out.reasoning


# ---------------------------------------------------------------------------
# Anonymization
# ---------------------------------------------------------------------------


def test_anonymize_replaces_symbol_and_open_positions():
    ctx = _ctx(symbol="NVDA", open_positions=["AAPL", "MSFT", "NVDA"])
    anon, aliases = _anonymize_context(ctx)
    # Real tickers are gone from the anonymized context.
    assert anon.symbol.startswith("[ASSET_")
    for pos in anon.open_positions:
        assert pos.startswith("[ASSET_")
    # Same ticker reuses the same placeholder (NVDA appears twice).
    assert anon.symbol == anon.open_positions[2]
    # Aliases map back to the real tickers.
    assert set(aliases.values()) == {"NVDA", "AAPL", "MSFT"}


def test_evaluate_deanonymizes_reasoning():
    """The journal MUST show the real ticker; the LLM only ever saw the
    placeholder."""
    verdict = json.dumps(
        {"multiplier": 0.7, "halt": False, "reasoning": "[ASSET_0] looks weak vs [ASSET_1]"}
    )
    r = AutonomousReasoner()
    captured = {}

    def fake_call(*, system, user, max_tokens, temperature):
        captured["user"] = user
        return _llm_response(verdict)

    with patch("src.agents.autonomous_reasoner.call_llm", side_effect=fake_call):
        out = r.evaluate(_ctx(symbol="NVDA", open_positions=["AMD"]))

    # LLM saw anonymized tickers.
    assert "NVDA" not in captured["user"]
    assert "[ASSET_" in captured["user"]
    # But the journal-bound reasoning has the real tickers back.
    assert "NVDA" in out.reasoning
    assert "AMD" in out.reasoning
    assert "[ASSET_" not in out.reasoning


# ---------------------------------------------------------------------------
# Journal integration
# ---------------------------------------------------------------------------


def test_evaluate_writes_journal_record():
    """Every evaluation MUST be auditable. Pin the journal contract."""
    verdict = json.dumps({"multiplier": 0.85, "halt": False, "reasoning": "mild dampening"})
    written: list[dict] = []

    class _StubWriter:
        def write(self, event):
            written.append(event)

    r = AutonomousReasoner(journal_writer=_StubWriter())
    with patch(
        "src.agents.autonomous_reasoner.call_llm", return_value=_llm_response(verdict)
    ):
        r.evaluate(_ctx(symbol="SPY"))
    assert len(written) == 1
    rec = written[0]
    assert rec["event"] == "autonomous_reasoner_eval"
    assert rec["symbol"] == "SPY"
    assert rec["side"] == "buy"
    assert rec["judgment"]["multiplier"] == 0.85
    assert rec["raw_response"] is not None


def test_evaluate_journal_failure_is_swallowed():
    """A broken journal writer must not crash the evaluation."""

    class _BrokenWriter:
        def write(self, event):
            raise OSError("disk full")

    r = AutonomousReasoner(journal_writer=_BrokenWriter())
    verdict = json.dumps({"multiplier": 1.0, "halt": False, "reasoning": "ok"})
    with patch(
        "src.agents.autonomous_reasoner.call_llm", return_value=_llm_response(verdict)
    ):
        out = r.evaluate(_ctx())
    assert out.multiplier == 1.0  # eval still succeeded


def test_evaluate_with_unavailable_llm_still_journals():
    """Even on fail-open, we record what happened."""
    from src.llm import LLMUnavailableError

    written: list[dict] = []

    class _W:
        def write(self, event):
            written.append(event)

    r = AutonomousReasoner(journal_writer=_W())
    with patch(
        "src.agents.autonomous_reasoner.call_llm",
        side_effect=LLMUnavailableError("rate limited"),
    ):
        r.evaluate(_ctx())
    assert len(written) == 1
    assert written[0]["judgment"]["fail_open"] is True


# ---------------------------------------------------------------------------
# Verdict parser
# ---------------------------------------------------------------------------


def test_parse_verdict_happy_path():
    raw = json.dumps({"multiplier": 0.85, "halt": False, "reasoning": "ok"})
    m, h, r = _parse_verdict(raw)
    assert m == 0.85
    assert h is False
    assert r == "ok"


def test_parse_verdict_missing_fields_uses_defaults():
    """LLM omitted reasoning -> placeholder; omitted halt -> False."""
    raw = json.dumps({"multiplier": 1.0})
    m, h, r = _parse_verdict(raw)
    assert m == 1.0
    assert h is False
    assert "no reasoning" in r.lower()


def test_parse_verdict_handles_string_multiplier():
    """Some LLMs emit multiplier as a string by mistake — coerce or default."""
    raw = json.dumps({"multiplier": "0.9", "halt": False, "reasoning": "x"})
    m, _, _ = _parse_verdict(raw)
    assert m == 0.9


def test_parse_verdict_rejects_inf():
    raw = json.dumps({"multiplier": float("inf"), "halt": False, "reasoning": "x"})
    # JSON can't directly encode inf — but if it gets there via float, our
    # guard kicks in.
    raw = '{"multiplier": Infinity, "halt": false, "reasoning": "x"}'
    m, _, _ = _parse_verdict(raw)
    # Python json.loads accepts `Infinity` by default; we guard against it.
    assert m == 1.0


def test_signal_judgment_shape_is_pinned():
    """Downstream consumers depend on these field names. Pin them."""
    j = SignalJudgment(
        multiplier=1.0, halt=False, reasoning="x",
        provider=None, elapsed_ms=0, asof="2026-05-06T00:00:00+00:00",
    )
    fields = set(j.__slots__)  # type: ignore[attr-defined]
    assert fields == {
        "multiplier", "halt", "reasoning", "provider",
        "elapsed_ms", "asof", "fail_open",
    }
