"""Tests for ``src.runtime.trade_pipeline.TradePipeline``.

The pipeline is exhaustively mocked: a fake broker that records submits,
a fake journal that records writes, a fake snapshot provider with a
hard-coded :class:`PortfolioSnapshot`, and an optional fake reasoner
returning canned :class:`SignalJudgment` values.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pandas as pd
import pytest
from src.agents.autonomous_reasoner import SignalJudgment
from src.agents.base import Agent, AssetClass
from src.execution.broker import BrokerSubmitError
from src.execution.orders import Submission
from src.risk.limits import PortfolioSnapshot
from src.runtime.trade_pipeline import (
    BrokerSnapshotProvider,
    ExecutionReport,
    ExecutionStep,
    StaticSnapshotProvider,
    TradePipeline,
)
from src.strategies.base import Signal

# ---------------------------------------------------------------------------
# Fakes.
# ---------------------------------------------------------------------------


class FakeBroker:
    """In-memory PaperBroker stand-in.

    Records every submitted ``Order`` and ``ApprovalToken`` so tests can
    inspect them. Submission can be configured to raise.
    """

    def __init__(self) -> None:
        self.submitted: list[tuple[Any, Any]] = []
        self.raise_on_submit: Exception | None = None

    def submit(self, order: Any, token: Any) -> Submission:
        if self.raise_on_submit is not None:
            raise self.raise_on_submit
        self.submitted.append((order, token))
        return Submission(
            broker_order_id=f"broker-{len(self.submitted)}",
            client_order_id=order.client_order_id,
            accepted_at=datetime.now(UTC),
            status="accepted",
        )


class FakeJournal:
    """Captures every event written so tests can assert on refusals."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def write(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class FakeAgent(Agent):
    """Minimal agent that returns a configured signal list."""

    name = "fake_agent"
    asset_class = AssetClass.EQUITY

    def __init__(
        self,
        signals: list[Signal],
        *,
        asset_class: AssetClass = AssetClass.EQUITY,
        raise_on_evaluate: Exception | None = None,
    ) -> None:
        super().__init__(strategies=[], universe=())
        self._signals = signals
        self.asset_class = asset_class  # type: ignore[misc]
        self._raise = raise_on_evaluate

    def evaluate(self, bars: dict[str, Any]) -> list[Signal]:
        if self._raise is not None:
            raise self._raise
        return list(self._signals)


class FakeReasoner:
    """Returns either a single judgment or an iterable per call."""

    def __init__(self, judgments: list[SignalJudgment] | SignalJudgment) -> None:
        if isinstance(judgments, SignalJudgment):
            self._judgments = [judgments]
            self._cycle = True
        else:
            self._judgments = list(judgments)
            self._cycle = False
        self._idx = 0
        self.calls: list[Any] = []

    def evaluate(self, ctx: Any) -> SignalJudgment:
        self.calls.append(ctx)
        if not self._judgments:
            return _identity_judgment()
        if self._cycle:
            return self._judgments[0]
        if self._idx >= len(self._judgments):
            return _identity_judgment()
        j = self._judgments[self._idx]
        self._idx += 1
        return j


# ---------------------------------------------------------------------------
# Builders.
# ---------------------------------------------------------------------------


def _identity_judgment(multiplier: float = 1.0, halt: bool = False) -> SignalJudgment:
    return SignalJudgment(
        multiplier=multiplier,
        halt=halt,
        reasoning="test judgment",
        provider="test",
        elapsed_ms=1,
        asof=datetime.now(UTC).isoformat(),
    )


def _signal(
    symbol: str = "SPY",
    side: str = "buy",
    entry: float = 100.0,
    stop: float = 95.0,
    target: float | None = 110.0,
    confidence: float = 0.6,
    strategy_tag: str = "test_strategy",
) -> Signal:
    return Signal(
        symbol=symbol,
        side=side,  # type: ignore[arg-type]
        entry=Decimal(str(entry)),
        stop=Decimal(str(stop)),
        target=Decimal(str(target)) if target is not None else None,
        confidence=confidence,
        strategy_tag=strategy_tag,
        timestamp=datetime.now(UTC),
    )


def _snapshot(equity: float = 100_000.0) -> PortfolioSnapshot:
    eq = Decimal(str(equity))
    return PortfolioSnapshot(
        equity=eq,
        cash=eq,
        realized_pnl_today=Decimal("0"),
        unrealized_pnl_today=Decimal("0"),
        trailing_peak_equity=eq,
        open_positions=(),
    )


def _build_pipeline(
    broker: FakeBroker | None = None,
    journal: FakeJournal | None = None,
    snapshot: PortfolioSnapshot | None = None,
    reasoner: FakeReasoner | None = None,
    outcome_capture: Any = None,
) -> tuple[TradePipeline, FakeBroker, FakeJournal]:
    broker = broker or FakeBroker()
    journal = journal or FakeJournal()
    snap = snapshot or _snapshot()
    pipe = TradePipeline(
        broker=broker,  # type: ignore[arg-type]
        journal_writer=journal,
        snapshot_provider=StaticSnapshotProvider(snap),
        reasoner=reasoner,
        outcome_capture=outcome_capture,
    )
    return pipe, broker, journal


def _refusal_events(journal: FakeJournal) -> list[dict[str, Any]]:
    return [e for e in journal.events if e.get("event") == "refusal"]


# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------


def test_run_for_with_no_signals_returns_empty_report() -> None:
    pipe, broker, journal = _build_pipeline()
    agent = FakeAgent(signals=[])
    report = pipe.run_for(agent, bars={})
    assert isinstance(report, ExecutionReport)
    assert report.agent_name == "fake_agent"
    assert report.n_signals == 0
    assert report.n_submitted == 0
    assert report.n_refused == 0
    assert report.steps == []
    assert broker.submitted == []
    assert _refusal_events(journal) == []


def test_run_for_submits_approved_signal() -> None:
    pipe, broker, _journal = _build_pipeline()
    sig = _signal(symbol="SPY", entry=100.0, stop=95.0, confidence=0.7)
    agent = FakeAgent(signals=[sig])

    report = pipe.run_for(agent, bars={})

    assert report.n_signals == 1
    assert report.n_submitted == 1
    assert report.n_refused == 0
    assert len(broker.submitted) == 1
    order, token = broker.submitted[0]
    assert order.symbol == "SPY"
    assert order.side == "buy"
    assert order.qty > 0
    assert order.order_type == "market"
    assert order.time_in_force == "day"
    assert token.risk_reason  # non-empty
    assert token.compliance_reason  # non-empty

    step = report.steps[0]
    assert step.submitted is True
    assert step.refusal_reason is None
    assert step.submission is not None
    # No reasoner configured -> multiplier is None.
    assert step.reasoner_multiplier is None


def test_run_for_skips_when_reasoner_halts() -> None:
    halt = SignalJudgment(
        multiplier=0.5,
        halt=True,
        reasoning="bad regime",
        provider="test",
        elapsed_ms=1,
        asof=datetime.now(UTC).isoformat(),
    )
    pipe, broker, journal = _build_pipeline(reasoner=FakeReasoner(halt))
    agent = FakeAgent(signals=[_signal()])

    report = pipe.run_for(agent, bars={})

    assert report.n_signals == 1
    assert report.n_submitted == 0
    assert report.n_refused == 1
    assert broker.submitted == []
    refusals = _refusal_events(journal)
    assert len(refusals) == 1
    assert refusals[0]["reason"] == "reasoner_halt"
    assert "bad regime" in refusals[0]["detail"]
    step = report.steps[0]
    assert step.reasoner_halt is True
    assert step.submitted is False
    assert step.refusal_reason == "reasoner_halt"


def test_run_for_dampens_when_reasoner_returns_low_multiplier() -> None:
    """Multiplier 0.5 is below the dampened threshold; signal still flows
    AND a ``reasoner_dampened`` refusal event is emitted for visibility."""
    judgment = SignalJudgment(
        multiplier=0.5,
        halt=False,
        reasoning="low conviction",
        provider="test",
        elapsed_ms=1,
        asof=datetime.now(UTC).isoformat(),
    )
    pipe, broker, journal = _build_pipeline(reasoner=FakeReasoner(judgment))
    sig = _signal(confidence=0.8)
    agent = FakeAgent(signals=[sig])

    report = pipe.run_for(agent, bars={})

    # Signal still submitted.
    assert report.n_submitted == 1
    assert len(broker.submitted) == 1
    # ... AND a dampened refusal was logged.
    refusals = _refusal_events(journal)
    assert any(r["reason"] == "reasoner_dampened" for r in refusals)
    step = report.steps[0]
    assert step.reasoner_multiplier == pytest.approx(0.5)
    assert step.final_confidence == pytest.approx(0.8 * 0.5)
    assert step.submitted is True


def test_run_for_skips_when_risk_gate_denies() -> None:
    """Tiny equity forces position_size to round to zero -> denial."""
    pipe, broker, journal = _build_pipeline(snapshot=_snapshot(equity=10.0))
    agent = FakeAgent(signals=[_signal(entry=100.0, stop=95.0)])

    report = pipe.run_for(agent, bars={})

    assert report.n_submitted == 0
    assert report.n_refused == 1
    assert broker.submitted == []
    refusals = _refusal_events(journal)
    assert len(refusals) == 1
    # Tiny-equity case maps to position cap by our reason classifier.
    assert refusals[0]["reason"] in {
        "risk_cap_position",
        "risk_cap_portfolio",
    }
    step = report.steps[0]
    assert step.submitted is False
    assert step.risk_decision_reason is not None


def test_run_for_logs_refusal_when_broker_raises() -> None:
    broker = FakeBroker()
    broker.raise_on_submit = BrokerSubmitError("alpaca down")
    pipe, broker, journal = _build_pipeline(broker=broker)
    sig1 = _signal(symbol="SPY")
    sig2 = _signal(symbol="QQQ")
    agent = FakeAgent(signals=[sig1, sig2])

    report = pipe.run_for(agent, bars={})

    assert report.n_signals == 2
    assert report.n_submitted == 0
    assert report.n_refused == 2
    refusals = _refusal_events(journal)
    assert len(refusals) == 2
    for r in refusals:
        assert r["reason"] == "broker_rejected"
        assert "alpaca down" in r["detail"]
    # All steps refused, none submitted, but the cycle finished cleanly.
    for step in report.steps:
        assert step.submitted is False
        assert step.refusal_reason == "broker_rejected"


def test_run_for_continues_after_one_signal_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Three signals; the middle one's context build raises. The others
    must still process and one must successfully submit."""

    class FlakyReasoner:
        def __init__(self) -> None:
            self.calls = 0

        def evaluate(self, ctx: Any) -> SignalJudgment:
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("boom in reasoner")
            return _identity_judgment()

    pipe, broker, journal = _build_pipeline(reasoner=FlakyReasoner())  # type: ignore[arg-type]
    sigs = [
        _signal(symbol="SPY"),
        _signal(symbol="QQQ"),
        _signal(symbol="IWM"),
    ]
    agent = FakeAgent(signals=sigs)

    report = pipe.run_for(agent, bars={})

    # 3 signals processed, signal #2 became a pipeline-error refusal,
    # the other two submitted successfully.
    assert report.n_signals == 3
    assert report.n_submitted == 2
    assert report.n_refused == 1
    submitted_symbols = {o.symbol for o, _ in broker.submitted}
    assert submitted_symbols == {"SPY", "IWM"}
    refusals = _refusal_events(journal)
    assert any(r.get("symbol") == "QQQ" for r in refusals)


def test_run_for_maps_crypto_symbol_at_submit() -> None:
    # Use a tighter price/stop so the position_size cap permits a non-zero
    # adjusted_size on the default 100k test equity.
    pipe, broker, _ = _build_pipeline()
    sig = _signal(symbol="BTCUSDT", entry=100.0, stop=99.0)
    agent = FakeAgent(signals=[sig], asset_class=AssetClass.CRYPTO)

    pipe.run_for(agent, bars={})

    assert len(broker.submitted) == 1
    order, _ = broker.submitted[0]
    assert order.symbol == "BTC/USD"
    # Crypto orders use GTC.
    assert order.time_in_force == "gtc"


def test_run_for_does_not_map_equity_symbol() -> None:
    pipe, broker, _ = _build_pipeline()
    sig = _signal(symbol="SPY")
    agent = FakeAgent(signals=[sig], asset_class=AssetClass.EQUITY)

    pipe.run_for(agent, bars={})

    assert len(broker.submitted) == 1
    order, _ = broker.submitted[0]
    assert order.symbol == "SPY"
    assert order.time_in_force == "day"


def test_run_for_calls_outcome_capture_when_position_closed() -> None:
    """Pre-cycle snapshot has SPY position; post-cycle does not.

    The pipeline must observe the closure and call outcome_capture.record
    (even if v1 only logs a warning + passes a deferred dict; the test
    confirms the call is made when a position truly closed)."""

    class TogglingProvider:
        def __init__(self) -> None:
            self.calls = 0
            self._with = PortfolioSnapshot(
                equity=Decimal("100000"),
                cash=Decimal("100000"),
                realized_pnl_today=Decimal("0"),
                unrealized_pnl_today=Decimal("0"),
                trailing_peak_equity=Decimal("100000"),
                open_positions=({"symbol": "SPY", "qty": 10},),
            )
            self._without = replace(self._with, open_positions=())

        def snapshot(self) -> PortfolioSnapshot:
            self.calls += 1
            # First call: BEFORE the cycle (has SPY).
            # Subsequent calls during signal processing & after: no SPY.
            return self._with if self.calls == 1 else self._without

    class CapturingOutcome:
        def __init__(self) -> None:
            self.calls: list[Any] = []

        def record(self, payload: Any) -> Any:
            self.calls.append(payload)
            return None

    journal = FakeJournal()
    broker = FakeBroker()
    capture = CapturingOutcome()
    pipe = TradePipeline(
        broker=broker,  # type: ignore[arg-type]
        journal_writer=journal,
        snapshot_provider=TogglingProvider(),
        outcome_capture=capture,
    )
    agent = FakeAgent(signals=[])  # zero signals, just exercise the diff

    pipe.run_for(agent, bars={})

    # SPY closed during the cycle -> outcome capture saw the closure.
    assert len(capture.calls) == 1
    payload = capture.calls[0]
    assert "SPY" in (payload.get("closed_symbols") or [])


def test_execution_report_shape_is_pinned() -> None:
    pipe, _, _ = _build_pipeline()
    sig = _signal()
    agent = FakeAgent(signals=[sig])

    report = pipe.run_for(agent, bars={})

    # Required top-level fields.
    assert hasattr(report, "agent_name")
    assert hasattr(report, "asof")
    assert hasattr(report, "n_signals")
    assert hasattr(report, "n_submitted")
    assert hasattr(report, "n_refused")
    assert hasattr(report, "steps")
    # n_signals == n_submitted + n_refused.
    assert report.n_signals == report.n_submitted + report.n_refused
    # Each step has the required fields.
    for step in report.steps:
        assert isinstance(step, ExecutionStep)
        for attr in (
            "symbol",
            "side",
            "strategy",
            "rule_confidence",
            "reasoner_multiplier",
            "reasoner_halt",
            "final_confidence",
            "risk_decision_reason",
            "submitted",
            "submission",
            "refusal_reason",
            "refusal_detail",
        ):
            assert hasattr(step, attr)
    # to_dict round-trips without raising.
    d = report.to_dict()
    assert isinstance(d, dict)
    assert d["n_signals"] == report.n_signals


def test_run_for_without_reasoner_skips_reasoner_step() -> None:
    pipe, _broker, _ = _build_pipeline(reasoner=None)
    agent = FakeAgent(signals=[_signal()])

    report = pipe.run_for(agent, bars={})

    assert report.n_submitted == 1
    step = report.steps[0]
    assert step.reasoner_multiplier is None
    assert step.reasoner_halt is False
    # Without a reasoner we don't multiply confidence.
    assert step.final_confidence == pytest.approx(step.rule_confidence)


def test_run_for_without_outcome_capture_no_op_on_position_close() -> None:
    """Having no outcome_capture must not crash even when positions close."""

    class TogglingProvider:
        def __init__(self) -> None:
            self.calls = 0
            self._with = PortfolioSnapshot(
                equity=Decimal("100000"),
                cash=Decimal("100000"),
                realized_pnl_today=Decimal("0"),
                unrealized_pnl_today=Decimal("0"),
                trailing_peak_equity=Decimal("100000"),
                open_positions=({"symbol": "SPY", "qty": 10},),
            )
            self._without = replace(self._with, open_positions=())

        def snapshot(self) -> PortfolioSnapshot:
            self.calls += 1
            return self._with if self.calls == 1 else self._without

    pipe = TradePipeline(
        broker=FakeBroker(),  # type: ignore[arg-type]
        journal_writer=FakeJournal(),
        snapshot_provider=TogglingProvider(),
        outcome_capture=None,
    )
    agent = FakeAgent(signals=[])
    # Must not raise.
    report = pipe.run_for(agent, bars={})
    assert report.n_signals == 0


# ---------------------------------------------------------------------------
# Auxiliary tests for the surrounding components.
# ---------------------------------------------------------------------------


def test_signal_context_includes_recent_bars() -> None:
    """When bars are passed, the reasoner sees the last 20 OHLCV dicts."""
    judgment = _identity_judgment()
    reasoner = FakeReasoner(judgment)
    pipe, _, _ = _build_pipeline(reasoner=reasoner)

    df = pd.DataFrame(
        {
            "open": [100.0 + i for i in range(50)],
            "high": [101.0 + i for i in range(50)],
            "low": [99.0 + i for i in range(50)],
            "close": [100.5 + i for i in range(50)],
            "volume": [1000 * (i + 1) for i in range(50)],
        }
    )
    sig = _signal(symbol="SPY")
    agent = FakeAgent(signals=[sig])

    pipe.run_for(agent, bars={"SPY": df})

    assert len(reasoner.calls) == 1
    ctx = reasoner.calls[0]
    # 20 most recent rows.
    assert len(ctx.recent_bars) == 20
    last = ctx.recent_bars[-1]
    assert last["o"] == pytest.approx(149.0)
    assert last["c"] == pytest.approx(149.5)


def test_recent_bars_empty_when_df_missing() -> None:
    reasoner = FakeReasoner(_identity_judgment())
    pipe, _, _ = _build_pipeline(reasoner=reasoner)
    sig = _signal(symbol="SPY")
    agent = FakeAgent(signals=[sig])

    pipe.run_for(agent, bars={})

    ctx = reasoner.calls[0]
    assert ctx.recent_bars == []


def test_broker_snapshot_provider_reads_account_and_positions() -> None:
    class FakeBrokerWithAccount:
        def get_account(self) -> dict[str, Any]:
            return {"equity": 50_000, "cash": 25_000, "day_change_usd": -100}

        def get_positions(self) -> list[dict[str, Any]]:
            return [{"symbol": "SPY", "qty": 5, "side": "long"}]

    provider = BrokerSnapshotProvider(FakeBrokerWithAccount())
    snap = provider.snapshot()
    assert snap.equity == Decimal("50000")
    assert snap.cash == Decimal("25000")
    assert snap.unrealized_pnl_today == Decimal("-100")
    # open_positions preserved as-is.
    assert len(snap.open_positions) == 1


def test_broker_snapshot_positions_are_position_like() -> None:
    """Regression test for the production crash:
    ``'dict' object has no attribute 'open_risk'`` thrown by
    ``portfolio_heat()`` because BrokerSnapshotProvider was passing raw
    Alpaca-shaped dicts into ``open_positions``. Every signal was being
    refused with ``risk_cap_position`` for the wrong reason — the risk
    gate never even ran.

    Each open_position must satisfy the ``_PositionLike`` Protocol
    (i.e. expose ``.open_risk: Decimal``) so the heat math works.
    """
    from src.risk.sizing import portfolio_heat

    class FakeBrokerWithCryptoPosition:
        def get_account(self) -> dict[str, Any]:
            return {"equity": 100_000, "cash": 90_000}

        def get_positions(self) -> list[dict[str, Any]]:
            # Real shape from dashboard.api.broker_proxy.get_positions().
            return [
                {
                    "symbol": "ETHUSD",
                    "qty": 3.99,
                    "avg_entry_price": 2348.70,
                    "market_value": 9376.0,
                    "unrealized_pl": 14.0,
                    "unrealized_plpc": 0.0015,
                    "current_price": 2352.0,
                    "side": "long",
                }
            ]

    provider = BrokerSnapshotProvider(FakeBrokerWithCryptoPosition())
    snap = provider.snapshot()

    # Each position exposes .open_risk and the heat math doesn't crash.
    for p in snap.open_positions:
        assert hasattr(p, "open_risk"), f"position {p!r} missing open_risk"
        assert isinstance(p.open_risk, Decimal)
    heat = portfolio_heat(snap.open_positions, snap.equity)
    # Conservative estimate: market_value * MAX_PER_TRADE_RISK / equity
    # = 9376 * 0.01 / 100_000 ≈ 0.000938
    assert heat > Decimal("0")
    assert heat < Decimal("0.01")  # heat from one small position is well under 1%


def test_broker_snapshot_position_adapter_handles_missing_fields() -> None:
    """A degenerate dict (no market_value) must not crash the snapshot."""
    from src.risk.sizing import portfolio_heat

    class FakeBrokerSparsePositions:
        def get_account(self) -> dict[str, Any]:
            return {"equity": 100_000}

        def get_positions(self) -> list[dict[str, Any]]:
            return [
                {"symbol": "WEIRD", "qty": "not-a-number", "side": "long"},
                {"symbol": "EMPTY"},  # nothing useful
            ]

    provider = BrokerSnapshotProvider(FakeBrokerSparsePositions())
    snap = provider.snapshot()
    assert len(snap.open_positions) == 2
    for p in snap.open_positions:
        assert hasattr(p, "open_risk")
    heat = portfolio_heat(snap.open_positions, snap.equity)
    # Both positions degrade to open_risk=0; heat should be 0.
    assert heat == Decimal("0")


def test_position_adapter_uses_max_of_mark_and_book_to_block_ratchet() -> None:
    """Regression: when an existing position is in drawdown (mark < book),
    the cumulative-cap check must use BOOK value, not mark — otherwise
    a position right at the 10% cap appears under-cap as soon as price
    dips, and the bot keeps adding small increments each cycle. Live
    failure on 2026-05-07: DOGE crept from 90 487 to 90 878 units across
    several boundary-grinding cycles before this fix landed."""
    from src.runtime.trade_pipeline import _as_position_like

    # Position in drawdown: book = 100 * 100 = $10 000, mark = $9 000.
    drawdown = {
        "symbol": "DOGEUSD",
        "qty": "100",
        "avg_entry_price": "100",
        "market_value": "9000",
    }
    adapter = _as_position_like(drawdown)
    # Notional must be the book value ($10 000), not the depressed mark.
    assert adapter.notional == Decimal("10000"), (
        f"expected book value 10000 (max of mark and book) "
        f"but got {adapter.notional}"
    )

    # Position above water: mark > book → mark wins (no harm; this is the
    # honest mark-to-market value of the position).
    above_water = {
        "symbol": "DOGEUSD",
        "qty": "100",
        "avg_entry_price": "100",
        "market_value": "11000",
    }
    adapter = _as_position_like(above_water)
    assert adapter.notional == Decimal("11000")


def test_broker_snapshot_provider_tolerates_failure() -> None:
    class BrokenBroker:
        def get_account(self) -> dict[str, Any]:
            raise RuntimeError("offline")

        def get_positions(self) -> list[Any]:
            raise RuntimeError("offline")

    provider = BrokerSnapshotProvider(BrokenBroker())
    snap = provider.snapshot()
    # Default equity falls back to 100k so the pipeline can still answer.
    assert snap.equity == Decimal("100000")


def test_run_for_handles_agent_evaluate_raising() -> None:
    pipe, broker, journal = _build_pipeline()
    agent = FakeAgent(signals=[], raise_on_evaluate=RuntimeError("strategy bug"))

    report = pipe.run_for(agent, bars={})

    assert report.n_signals == 0
    assert broker.submitted == []
    refusals = _refusal_events(journal)
    assert any("strategy bug" in r.get("detail", "") for r in refusals)
