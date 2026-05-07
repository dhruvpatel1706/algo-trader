"""Signal-to-broker pipeline.

Runs one full evaluation cycle for a single agent::

    agent.evaluate(bars)            -> list[Signal]
        for each Signal:
            build SignalContext
            reasoner.evaluate(ctx)  -> SignalJudgment       (optional)
            if halt -> refusal + skip
            apply multiplier to confidence
            build ProposedTrade + PortfolioSnapshot
            check_limits()          -> Decision
            if not approve -> refusal + skip
            build ApprovalToken (compliance auto-approved for v1)
            map symbol for broker
            broker.submit(order, token)
            journal the submit

OutcomeCapture diff: best-effort scaffolding. We snapshot open positions
before and after the cycle and log a warning for any symbol that closed
during the cycle. Calling :meth:`OutcomeCapture.record` requires entry
price / time tracking we don't have plumbed yet, so v1 emits a marker
log line and defers the actual call until that data is available.

Fail-open everywhere: a single misbehaving signal must not break the
batch. Unexpected exceptions become refusal events with reason
``broker_rejected`` (when the failure was at the broker boundary) or
the closest applicable refusal reason; the cycle continues processing
the remaining signals.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol

import ulid

from src.agents.autonomous_reasoner import SignalContext
from src.agents.base import Agent, AssetClass
from src.execution.broker import ApprovalToken, BrokerSubmitError, PaperBroker
from src.execution.orders import Order, Submission
from src.journal.refusal_events import RefusalReason, log_refusal
from src.risk.limits import (
    PortfolioSnapshot,
    ProposedTrade,
    check_limits,
)
from src.runtime.symbol_map import map_symbol_for_broker

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas as pd

    from src.strategies.base import Signal

log = logging.getLogger(__name__)


__all__ = [
    "BrokerSnapshotProvider",
    "ExecutionReport",
    "ExecutionStep",
    "PortfolioSnapshotProvider",
    "StaticSnapshotProvider",
    "TradePipeline",
]


# A reasoner multiplier strictly below this threshold also emits a
# ``reasoner_dampened`` refusal even though the signal still flows. Mirrors
# ``src.agents.reasoner_filter.DAMPENED_REFUSAL_THRESHOLD`` so the two
# observability surfaces agree.
_DAMPENED_REFUSAL_THRESHOLD: float = 0.7

# Maximum recent bars surfaced to the reasoner per signal. Larger context =
# larger LLM cost; the reasoner's own ``MAX_CONTEXT_BARS`` is also 20.
_RECENT_BARS_FOR_REASONER: int = 20


# ---------------------------------------------------------------------------
# DTOs surfaced to the dashboard / journal.
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ExecutionStep:
    """One signal's journey through the pipeline.

    Renderable in the dashboard so operators can see why a particular
    signal was or wasn't submitted.
    """

    symbol: str
    side: str  # "buy" | "sell"
    strategy: str
    rule_confidence: float
    reasoner_multiplier: float | None  # None when reasoner not configured
    reasoner_halt: bool  # True if the reasoner vetoed
    final_confidence: float  # rule_confidence * (multiplier or 1.0)
    risk_decision_reason: str | None
    submitted: bool
    submission: Submission | None
    refusal_reason: str | None  # one of RefusalReason values, if not submitted
    refusal_detail: str | None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict for journal / dashboard use."""
        d = asdict(self)
        if self.submission is not None:
            sub = self.submission
            d["submission"] = {
                "broker_order_id": sub.broker_order_id,
                "client_order_id": sub.client_order_id,
                "accepted_at": sub.accepted_at.isoformat(),
                "status": sub.status,
            }
        return d


@dataclass(slots=True)
class ExecutionReport:
    """Summary of one ``run_for`` cycle."""

    agent_name: str
    asof: str  # ISO8601 UTC
    n_signals: int  # raw count from agent.evaluate
    n_submitted: int
    n_refused: int
    steps: list[ExecutionStep] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "asof": self.asof,
            "n_signals": self.n_signals,
            "n_submitted": self.n_submitted,
            "n_refused": self.n_refused,
            "steps": [s.to_dict() for s in self.steps],
        }


# ---------------------------------------------------------------------------
# Snapshot providers.
# ---------------------------------------------------------------------------


class PortfolioSnapshotProvider(Protocol):
    """Anything that can produce a :class:`PortfolioSnapshot` for the risk gate.

    Default impl reads from ``broker.get_account()`` +
    ``broker.get_positions()``. Tests may inject a static snapshot instead.
    """

    def snapshot(self) -> PortfolioSnapshot: ...


class StaticSnapshotProvider:
    """Test-friendly :class:`PortfolioSnapshotProvider` returning a fixed value."""

    def __init__(self, value: PortfolioSnapshot) -> None:
        self._value = value

    def snapshot(self) -> PortfolioSnapshot:
        return self._value


class BrokerSnapshotProvider:
    """Reads account + positions from a broker-shaped object.

    Accepts anything that exposes ``get_account()`` returning a dict-like
    with at least ``equity`` and ``cash``, and ``get_positions()``
    returning an iterable of dict-likes. The shape mirrors
    :class:`dashboard.api.broker_proxy.BrokerProxy`; a real
    :class:`src.execution.broker.PaperBroker` doesn't expose these
    methods directly, so the runner is expected to pass the proxy.

    The trailing-peak-equity field of :class:`PortfolioSnapshot` is the
    high-water-mark used to compute drawdown. We accept it via an
    injected callable so the runner can wire whatever HWM tracker it
    has (file, Redis, ML store). When not provided, we conservatively
    treat the current equity as the peak — that disables the drawdown
    halt for this snapshot, which is acceptable in v1 because
    ``DAILY_LOSS_HALT`` is the stronger gate inside the same trading
    day.
    """

    def __init__(
        self,
        broker: Any,
        trailing_peak_equity_provider: Any = None,  # callable[[], Decimal] | None
    ) -> None:
        self._broker = broker
        self._peak_provider = trailing_peak_equity_provider

    def snapshot(self) -> PortfolioSnapshot:
        acc: Any = {}
        try:
            acc = self._broker.get_account() or {}
        except Exception as e:
            log.warning("BrokerSnapshotProvider: get_account failed: %s", e)
            acc = {}

        positions: Any = ()
        try:
            positions = self._broker.get_positions() or ()
        except Exception as e:
            log.warning("BrokerSnapshotProvider: get_positions failed: %s", e)
            positions = ()

        equity = Decimal(str(acc.get("equity", 100_000)))
        cash = Decimal(str(acc.get("cash", equity)))
        # The proxy doesn't expose realized / unrealized splits today; treat
        # the day's change as unrealized for risk-gate purposes (worst-case
        # for the daily-loss check, which we want).
        day_change = Decimal(str(acc.get("day_change_usd", 0)))
        peak: Decimal
        if self._peak_provider is not None:
            try:
                peak = Decimal(str(self._peak_provider()))
            except Exception as e:
                log.warning("BrokerSnapshotProvider: peak provider failed: %s", e)
                peak = equity
        else:
            peak = equity
        return PortfolioSnapshot(
            equity=equity,
            cash=cash,
            realized_pnl_today=Decimal("0"),
            unrealized_pnl_today=day_change,
            trailing_peak_equity=peak,
            open_positions=tuple(_as_position_like(p) for p in positions),
        )


@dataclass(frozen=True, slots=True)
class _OpenPositionAdapter:
    """Adapter so broker-proxy dicts satisfy ``_PositionLike`` for risk gates.

    The broker proxy returns raw Alpaca-shaped dicts (``{"symbol": "ETHUSD",
    "qty": 3.99, "avg_entry_price": 2348.7, "market_value": 9376.0, …}``).
    ``src.risk.sizing.portfolio_heat()`` consumes positions through a
    Protocol that requires ``.open_risk: Decimal`` — a number we never
    actually track because the broker doesn't know our per-trade stop.

    We approximate ``open_risk`` as the position's notional times the
    per-trade risk cap. That's conservative on purpose (the real value
    is bounded above by exactly this number when the trade is opened
    at-cap), so the heat-check tends to over-state existing risk and
    falsely-reject extra trades rather than under-state and falsely-allow.

    ``symbol`` and ``notional`` are also carried so the cumulative-cap
    check in ``check_limits`` can find existing exposure for the symbol
    being traded. Empty string + 0 are the safe defaults when we couldn't
    parse the broker dict.
    """

    open_risk: Decimal
    symbol: str = ""
    notional: Decimal = Decimal("0")


def _as_position_like(p: Any) -> Any:
    """Wrap a broker-proxy dict (or anything else) into ``_PositionLike``.

    If the input already has ``.open_risk``, pass it through. Otherwise
    derive a conservative open_risk from the dict's notional. Anything
    that can't be coerced returns ``_OpenPositionAdapter(open_risk=0)``
    so risk math doesn't blow up — the risk gate sees a position with
    zero open risk, which is harmless (the heat check just doesn't add
    anything for that row).
    """
    if hasattr(p, "open_risk"):
        return p
    if not isinstance(p, dict):
        return _OpenPositionAdapter(open_risk=Decimal("0"))
    try:
        # Use the MAX of mark-to-market (market_value) and book value
        # (qty * avg_entry_price) so the cumulative-cap check doesn't
        # leak budget when an existing position is in drawdown. Without
        # this, a position right at the 10% cap appears under cap as
        # soon as price dips, the cap check approves a tiny add, and
        # over many cycles the position ratchets up — observed live on
        # 2026-05-07 when DOGE crept from 90 487 → 90 878 units across
        # several boundary-grinding cycles. Both values can be missing
        # from a partial broker dict, so we degrade gracefully.
        mv_raw = p.get("market_value")
        qty_raw = p.get("qty", 0)
        entry_raw = p.get("avg_entry_price", 0)
        try:
            book = abs(Decimal(str(qty_raw)) * Decimal(str(entry_raw)))
        except (ArithmeticError, TypeError, ValueError):
            book = Decimal("0")
        try:
            mark = abs(Decimal(str(mv_raw))) if mv_raw is not None else Decimal("0")
        except (ArithmeticError, TypeError, ValueError):
            mark = Decimal("0")
        notional = max(mark, book)
        # Use the configured per-trade cap; if settings can't be read for any
        # reason, default to 0.01 (1%) which matches the v1 .env default.
        from src.config import get_settings  # noqa: PLC0415

        try:
            cap = Decimal(str(get_settings().MAX_PER_TRADE_RISK))
        except Exception:
            cap = Decimal("0.01")
        sym = str(p.get("symbol", "") or "")
        return _OpenPositionAdapter(
            open_risk=notional * cap,
            symbol=sym,
            notional=notional,
        )
    except (ArithmeticError, TypeError, ValueError):
        return _OpenPositionAdapter(open_risk=Decimal("0"))


def _existing_notional_in_symbol(positions: Any, symbol: str) -> Decimal:
    """Sum absolute notional held in ``symbol`` across ``positions``.

    Accepts both adapter instances (preferred shape, with ``.symbol`` and
    ``.notional``) and raw broker dicts (fallback). Symbol matching is
    normalized: 'ETHUSD' / 'ETH/USD' / 'ETHUSDT' all map to the same
    canonical form so we don't double-count when the broker reports one
    shape and the strategy proposes another.
    """
    target = _canonical_symbol(symbol)
    total = Decimal("0")
    for p in positions or ():
        sym = ""
        notional = Decimal("0")
        if hasattr(p, "symbol") and hasattr(p, "notional"):
            sym = str(getattr(p, "symbol", "") or "")
            try:
                notional = Decimal(str(getattr(p, "notional", 0) or 0))
            except (ArithmeticError, TypeError, ValueError):
                notional = Decimal("0")
        elif isinstance(p, dict):
            sym = str(p.get("symbol", "") or "")
            mv = p.get("market_value")
            if mv is None:
                try:
                    notional = Decimal(str(p.get("qty", 0))) * Decimal(
                        str(p.get("avg_entry_price", 0))
                    )
                except (ArithmeticError, TypeError, ValueError):
                    notional = Decimal("0")
            else:
                try:
                    notional = Decimal(str(mv))
                except (ArithmeticError, TypeError, ValueError):
                    notional = Decimal("0")
        if not sym:
            continue
        if _canonical_symbol(sym) == target:
            total += abs(notional)
    return total


def _canonical_symbol(symbol: str) -> str:
    """Normalize ETHUSD / ETH/USD / ETHUSDT / ETH-USD to a single form
    so the cap check doesn't double-count when broker and strategy
    disagree on quote-currency suffix shape."""
    s = symbol.upper().replace("/", "").replace("-", "")
    for quote in ("USDT", "USDC", "USD", "EUR", "BTC", "ETH"):
        if s.endswith(quote) and len(s) > len(quote):
            return f"{s[: -len(quote)]}USD"
    return s


# ---------------------------------------------------------------------------
# The pipeline itself.
# ---------------------------------------------------------------------------


class TradePipeline:
    """Run the full signal-to-broker cycle for one agent.

    Construction wires the dependencies; per-cycle work happens in
    :meth:`run_for`. The pipeline never raises into the caller — every
    failure becomes a refusal event so the operator dashboard tells the
    full story.
    """

    def __init__(
        self,
        broker: PaperBroker,
        journal_writer: Any,
        snapshot_provider: PortfolioSnapshotProvider,
        *,
        reasoner: Any = None,
        outcome_capture: Any = None,
        compliance_auto_approve_reason: str = (
            "v1 equity has no compliance gate beyond risk"
        ),
    ) -> None:
        self._broker = broker
        self._journal = journal_writer
        self._snapshot_provider = snapshot_provider
        self._reasoner = reasoner
        self._outcome_capture = outcome_capture
        self._compliance_auto_approve_reason = compliance_auto_approve_reason

    # -- public ----------------------------------------------------------

    def run_for(
        self,
        agent: Agent,
        bars: dict[str, pd.DataFrame],
    ) -> ExecutionReport:
        """Full cycle for one agent. Never raises — errors become refusals.

        Args:
            agent: The :class:`Agent` to evaluate.
            bars: Per-symbol OHLCV frames the agent's strategies need.
                Pass an empty dict if the agent ingests bars internally.

        Returns:
            An :class:`ExecutionReport` describing every signal the agent
            emitted and what happened to it.
        """
        asof = datetime.now(UTC).isoformat()

        # Diff the open-position set across the cycle so the post-mortem
        # capture knows which positions closed. This is best-effort in v1
        # because we don't have entry-time tracking plumbed everywhere.
        before_positions = self._open_position_symbols()

        try:
            signals = list(agent.evaluate(bars))
        except Exception as e:
            log.exception("trade_pipeline: agent.evaluate raised; treating as zero-signal")
            self._log_refusal_safely(
                reason="broker_rejected",
                agent_name=getattr(agent, "name", "<unknown>"),
                detail=f"agent.evaluate raised: {type(e).__name__}: {e}",
            )
            signals = []

        steps: list[ExecutionStep] = []
        for sig in signals:
            try:
                step = self._process_signal(sig, agent, bars)
            except Exception as e:
                # Last-resort guard. Anything still escaping must be turned
                # into a step so the cycle keeps moving.
                log.exception(
                    "trade_pipeline: unhandled exception while processing %s; "
                    "recording as pipeline error",
                    getattr(sig, "symbol", "<unknown>"),
                )
                step = ExecutionStep(
                    symbol=getattr(sig, "symbol", "<unknown>"),
                    side=getattr(sig, "side", "buy"),
                    strategy=getattr(sig, "strategy_tag", "<unknown>"),
                    rule_confidence=float(getattr(sig, "confidence", 0.0)),
                    reasoner_multiplier=None,
                    reasoner_halt=False,
                    final_confidence=float(getattr(sig, "confidence", 0.0)),
                    risk_decision_reason=None,
                    submitted=False,
                    submission=None,
                    refusal_reason="broker_rejected",
                    refusal_detail=f"pipeline_error: {type(e).__name__}: {e}",
                )
                self._log_refusal_safely(
                    reason="broker_rejected",
                    symbol=step.symbol,
                    side=step.side,
                    strategy=step.strategy,
                    agent_name=agent.name,
                    detail=step.refusal_detail or "",
                    extra={"exception_type": type(e).__name__},
                )
            steps.append(step)

        n_submitted = sum(1 for s in steps if s.submitted)
        n_refused = sum(1 for s in steps if not s.submitted)

        # OutcomeCapture diff: log closures for visibility. v1 cannot call
        # ``record`` because we don't have entry-price/time tracking yet.
        self._diff_and_log_closures(before_positions, agent_name=agent.name)

        return ExecutionReport(
            agent_name=agent.name,
            asof=asof,
            n_signals=len(signals),
            n_submitted=n_submitted,
            n_refused=n_refused,
            steps=steps,
        )

    # -- per-signal processing ------------------------------------------

    def _process_signal(
        self,
        sig: Signal,
        agent: Agent,
        bars: dict[str, pd.DataFrame],
    ) -> ExecutionStep:
        """Run one signal through reasoner -> risk -> broker.

        Returns a fully-populated :class:`ExecutionStep`. Refusals are
        journaled inside this method so the caller only assembles the
        report.
        """
        snapshot = self._snapshot_provider.snapshot()

        # ----- Reasoner --------------------------------------------------
        reasoner_multiplier: float | None = None
        reasoner_halt = False
        if self._reasoner is not None:
            ctx = self._build_signal_context(sig, agent, bars, snapshot)
            judgment = self._reasoner.evaluate(ctx)
            reasoner_multiplier = float(judgment.multiplier)
            reasoner_halt = bool(judgment.halt)

            if reasoner_halt:
                self._log_refusal_safely(
                    reason="reasoner_halt",
                    symbol=sig.symbol,
                    side=sig.side,
                    strategy=sig.strategy_tag,
                    agent_name=agent.name,
                    detail=judgment.reasoning,
                    extra={
                        "multiplier": reasoner_multiplier,
                        "provider": judgment.provider,
                        "fail_open": judgment.fail_open,
                    },
                )
                return _refused_step(
                    sig,
                    reasoner_multiplier=reasoner_multiplier,
                    reasoner_halt=True,
                    final_confidence=float(sig.confidence) * reasoner_multiplier,
                    refusal_reason="reasoner_halt",
                    refusal_detail=judgment.reasoning,
                )

            if reasoner_multiplier < _DAMPENED_REFUSAL_THRESHOLD:
                self._log_refusal_safely(
                    reason="reasoner_dampened",
                    symbol=sig.symbol,
                    side=sig.side,
                    strategy=sig.strategy_tag,
                    agent_name=agent.name,
                    detail=(
                        f"reasoner dampened multiplier={reasoner_multiplier:.3f} "
                        f"below threshold={_DAMPENED_REFUSAL_THRESHOLD}: "
                        f"{judgment.reasoning}"
                    ),
                    extra={
                        "multiplier": reasoner_multiplier,
                        "threshold": _DAMPENED_REFUSAL_THRESHOLD,
                    },
                )

        applied_multiplier = reasoner_multiplier if reasoner_multiplier is not None else 1.0
        final_confidence = max(0.0, min(1.0, float(sig.confidence) * applied_multiplier))

        # ----- Risk gate -------------------------------------------------
        proposed = ProposedTrade(
            symbol=sig.symbol,
            side=sig.side,
            entry=Decimal(str(sig.entry)),
            stop=Decimal(str(sig.stop)),
            target=Decimal(str(sig.target)) if sig.target is not None else None,
            strategy_tag=sig.strategy_tag,
        )
        try:
            decision = check_limits(
                proposed,
                snapshot,
                existing_notional_in_symbol=_existing_notional_in_symbol(
                    snapshot.open_positions, sig.symbol
                ),
            )
        except Exception as e:
            log.exception(
                "trade_pipeline: check_limits raised for %s; refusing", sig.symbol
            )
            detail = f"check_limits raised: {type(e).__name__}: {e}"
            self._log_refusal_safely(
                reason="risk_cap_position",
                symbol=sig.symbol,
                side=sig.side,
                strategy=sig.strategy_tag,
                agent_name=agent.name,
                detail=detail,
            )
            return _refused_step(
                sig,
                reasoner_multiplier=reasoner_multiplier,
                reasoner_halt=False,
                final_confidence=final_confidence,
                refusal_reason="risk_cap_position",
                refusal_detail=detail,
                risk_decision_reason=detail,
            )

        if not decision.approve:
            mapped_reason = _map_risk_reason(decision.reason)
            self._log_refusal_safely(
                reason=mapped_reason,
                symbol=sig.symbol,
                side=sig.side,
                strategy=sig.strategy_tag,
                agent_name=agent.name,
                detail=decision.reason,
            )
            return _refused_step(
                sig,
                reasoner_multiplier=reasoner_multiplier,
                reasoner_halt=False,
                final_confidence=final_confidence,
                refusal_reason=mapped_reason,
                refusal_detail=decision.reason,
                risk_decision_reason=decision.reason,
            )

        qty = decision.adjusted_size or 0
        if qty <= 0:
            detail = f"risk decision approved but adjusted_size={qty}"
            self._log_refusal_safely(
                reason="risk_cap_position",
                symbol=sig.symbol,
                side=sig.side,
                strategy=sig.strategy_tag,
                agent_name=agent.name,
                detail=detail,
            )
            return _refused_step(
                sig,
                reasoner_multiplier=reasoner_multiplier,
                reasoner_halt=False,
                final_confidence=final_confidence,
                refusal_reason="risk_cap_position",
                refusal_detail=detail,
                risk_decision_reason=detail,
            )

        # ----- Approval token + broker submit ---------------------------
        token = ApprovalToken(
            cycle_id=str(ulid.ULID()),
            risk_decision_ts=datetime.now(UTC),
            compliance_decision_ts=datetime.now(UTC),
            risk_reason=decision.reason,
            compliance_reason=self._compliance_auto_approve_reason,
        )

        try:
            broker_symbol = map_symbol_for_broker(sig.symbol, agent.asset_class)
        except ValueError as e:
            detail = f"symbol map failed: {e}"
            self._log_refusal_safely(
                reason="broker_rejected",
                symbol=sig.symbol,
                side=sig.side,
                strategy=sig.strategy_tag,
                agent_name=agent.name,
                detail=detail,
            )
            return _refused_step(
                sig,
                reasoner_multiplier=reasoner_multiplier,
                reasoner_halt=False,
                final_confidence=final_confidence,
                refusal_reason="broker_rejected",
                refusal_detail=detail,
                risk_decision_reason=decision.reason,
            )

        order = Order(
            client_order_id=str(ulid.ULID()),
            symbol=broker_symbol,
            qty=int(qty),
            side=sig.side,
            order_type="market",
            time_in_force=("gtc" if agent.asset_class == AssetClass.CRYPTO else "day"),
            limit_price=None,
            extended_hours=False,
            strategy_tag=sig.strategy_tag,
        )

        try:
            submission = self._broker.submit(order, token)
        except BrokerSubmitError as e:
            detail = f"broker submit failed: {e}"
            self._log_refusal_safely(
                reason="broker_rejected",
                symbol=sig.symbol,
                side=sig.side,
                strategy=sig.strategy_tag,
                agent_name=agent.name,
                detail=detail,
                extra={"broker_symbol": broker_symbol},
            )
            return _refused_step(
                sig,
                reasoner_multiplier=reasoner_multiplier,
                reasoner_halt=False,
                final_confidence=final_confidence,
                refusal_reason="broker_rejected",
                refusal_detail=detail,
                risk_decision_reason=decision.reason,
            )
        except Exception as e:
            # Defensive: PermissionError or anything else that escapes the
            # broker layer (PaperBroker.submit is documented to raise
            # PermissionError on a malformed token, which is a code bug
            # but shouldn't crash the pipeline either).
            log.exception(
                "trade_pipeline: unexpected broker error for %s",
                sig.symbol,
            )
            detail = f"broker unexpected error: {type(e).__name__}: {e}"
            self._log_refusal_safely(
                reason="broker_rejected",
                symbol=sig.symbol,
                side=sig.side,
                strategy=sig.strategy_tag,
                agent_name=agent.name,
                detail=detail,
            )
            return _refused_step(
                sig,
                reasoner_multiplier=reasoner_multiplier,
                reasoner_halt=False,
                final_confidence=final_confidence,
                refusal_reason="broker_rejected",
                refusal_detail=detail,
                risk_decision_reason=decision.reason,
            )

        # Journal the successful submit. Best-effort.
        try:
            self._journal.write(
                {
                    "event": "trade_submit",
                    "agent": agent.name,
                    "symbol": sig.symbol,
                    "broker_symbol": broker_symbol,
                    "side": sig.side,
                    "qty": int(qty),
                    "strategy": sig.strategy_tag,
                    "broker_order_id": submission.broker_order_id,
                    "client_order_id": submission.client_order_id,
                    "rule_confidence": float(sig.confidence),
                    "reasoner_multiplier": reasoner_multiplier,
                    "final_confidence": final_confidence,
                }
            )
        except Exception as e:
            log.warning("trade_pipeline: journal write failed for submit: %s", e)

        return ExecutionStep(
            symbol=sig.symbol,
            side=sig.side,
            strategy=sig.strategy_tag,
            rule_confidence=float(sig.confidence),
            reasoner_multiplier=reasoner_multiplier,
            reasoner_halt=False,
            final_confidence=final_confidence,
            risk_decision_reason=decision.reason,
            submitted=True,
            submission=submission,
            refusal_reason=None,
            refusal_detail=None,
        )

    # -- helpers ---------------------------------------------------------

    def _build_signal_context(
        self,
        sig: Signal,
        agent: Agent,
        bars: dict[str, pd.DataFrame],
        snapshot: PortfolioSnapshot,
    ) -> SignalContext:
        """Construct the :class:`SignalContext` the reasoner consumes."""
        df = bars.get(sig.symbol) if isinstance(bars, dict) else None
        recent_bars = _recent_bar_dicts(df, n=_RECENT_BARS_FOR_REASONER)
        # snapshot.open_positions has loose shape (tuple of dicts/objects);
        # extract symbols defensively.
        open_symbols: list[str] = []
        for p in snapshot.open_positions:
            if isinstance(p, dict):
                sym = p.get("symbol")
            else:
                sym = getattr(p, "symbol", None)
            if sym:
                open_symbols.append(str(sym))

        # Translate the AssetClass StrEnum into the literal string the
        # reasoner expects.
        asset_class_str = (
            agent.asset_class.value
            if isinstance(agent.asset_class, AssetClass)
            else str(agent.asset_class)
        )

        return SignalContext(
            symbol=sig.symbol,
            side=sig.side,
            strategy=sig.strategy_tag,
            rule_confidence=float(sig.confidence),
            entry_price=float(sig.entry),
            stop_price=float(sig.stop),
            target_price=float(sig.target) if sig.target is not None else None,
            recent_bars=recent_bars,
            open_positions=open_symbols,
            asset_class=asset_class_str,  # type: ignore[arg-type]
        )

    def _open_position_symbols(self) -> dict[str, int]:
        """Best-effort snapshot of ``symbol -> qty`` for the OutcomeCapture diff."""
        try:
            snap = self._snapshot_provider.snapshot()
        except Exception as e:
            log.warning("trade_pipeline: pre-cycle snapshot failed: %s", e)
            return {}
        out: dict[str, int] = {}
        for p in snap.open_positions:
            if isinstance(p, dict):
                sym = p.get("symbol")
                qty = p.get("qty", 0)
            else:
                sym = getattr(p, "symbol", None)
                qty = getattr(p, "qty", 0)
            if not sym:
                continue
            try:
                out[str(sym)] = int(float(qty))
            except (TypeError, ValueError):
                out[str(sym)] = 0
        return out

    def _diff_and_log_closures(
        self,
        before: dict[str, int],
        *,
        agent_name: str,
    ) -> None:
        """Identify positions that closed during the cycle.

        v1 limitation: we don't have ``entry_ts`` / ``entry_price`` queryable
        from a single source of truth, so we cannot synthesize a complete
        :class:`src.memory.post_mortem.ClosedTrade` record yet. Until that's
        plumbed, we emit a marker log line so the operator can see closures
        in real time, and call :meth:`OutcomeCapture.record` only when the
        injected hook is itself a stub that accepts the abbreviated payload
        (the production :class:`OutcomeCapture` will not — that's intentional;
        we want this path to be a no-op until the data is available).
        """
        after = self._open_position_symbols()
        closed = [s for s, q in before.items() if s not in after or after.get(s, 0) == 0]
        if not closed:
            return
        log.warning(
            "trade_pipeline: %d position(s) closed during cycle for agent=%s "
            "(symbols=%s); outcome capture deferred — entry-tracking not "
            "yet available end-to-end",
            len(closed),
            agent_name,
            closed,
        )
        if self._outcome_capture is None:
            return
        # We pass the symbol list to the capture object. The production
        # OutcomeCapture expects a fully-formed ClosedTrade and will not
        # accept this shape — that's intentional for v1: only test stubs
        # configured to accept the abbreviated payload will see the call.
        try:
            self._outcome_capture.record(  # type: ignore[call-arg]
                {
                    "agent": agent_name,
                    "closed_symbols": closed,
                    "asof": datetime.now(UTC).isoformat(),
                    "deferred": True,
                }
            )
        except TypeError:
            # Production OutcomeCapture has a typed ClosedTrade signature
            # that won't accept our v1 best-effort dict; that's expected.
            log.debug(
                "trade_pipeline: outcome_capture rejected v1 deferred payload "
                "(expected; no entry-tracking yet)"
            )
        except Exception as e:
            log.warning("trade_pipeline: outcome_capture.record raised: %s", e)

    def _log_refusal_safely(
        self,
        *,
        reason: RefusalReason,
        symbol: str | None = None,
        side: str | None = None,
        strategy: str | None = None,
        agent_name: str | None = None,
        detail: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Wrap ``log_refusal`` so a journal failure cannot break the pipeline."""
        try:
            log_refusal(
                self._journal,
                reason=reason,
                symbol=symbol,
                side=side,
                strategy=strategy,
                agent=agent_name,
                detail=detail,
                extra=extra,
            )
        except Exception as e:
            log.warning("trade_pipeline: log_refusal swallowed exception: %s", e)


# ---------------------------------------------------------------------------
# Module-level helpers (kept private; tests can import via the module).
# ---------------------------------------------------------------------------


def _recent_bar_dicts(df: Any, n: int) -> list[dict[str, Any]]:
    """Convert the last ``n`` rows of an OHLCV frame to a list of dicts.

    Defensive — a missing or empty frame yields ``[]`` so the reasoner
    sees an honest "insufficient context" signal rather than a partial
    fabricated context.

    Each output dict has keys ``ts``, ``o``, ``h``, ``l``, ``c``, ``v``;
    missing columns become ``None``.
    """
    if df is None:
        return []
    try:
        n_rows = len(df)
    except TypeError:
        return []
    if n_rows == 0:
        return []
    try:
        tail = df.tail(n)
    except Exception:
        return []

    rows: list[dict[str, Any]] = []
    # Iterate via itertuples so we don't depend on a specific column order.
    try:
        for idx, row in tail.iterrows():
            rows.append(
                {
                    "ts": str(idx) if idx is not None else None,
                    "o": _coerce_float(row.get("open") if hasattr(row, "get") else None),
                    "h": _coerce_float(row.get("high") if hasattr(row, "get") else None),
                    "l": _coerce_float(row.get("low") if hasattr(row, "get") else None),
                    "c": _coerce_float(row.get("close") if hasattr(row, "get") else None),
                    "v": _coerce_float(row.get("volume") if hasattr(row, "get") else None),
                }
            )
    except Exception:
        return []
    return rows


def _coerce_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _map_risk_reason(reason: str) -> RefusalReason:
    """Map a risk-gate ``Decision.reason`` to a :data:`RefusalReason` literal.

    The risk gate emits human-readable strings (``"portfolio heat ..."``,
    ``"drawdown ..."``, ``"notional ... > MAX_SINGLE_POSITION ..."``); we
    classify them so the dashboard can group refusals consistently.
    """
    lowered = reason.lower()
    if "drawdown" in lowered:
        return "coherence_halt"
    if "intraday" in lowered or "daily" in lowered:
        return "daily_loss_breach"
    if "heat" in lowered or "portfolio" in lowered:
        return "risk_cap_portfolio"
    if "notional" in lowered or "size" in lowered or "position" in lowered:
        return "risk_cap_position"
    return "risk_cap_position"


def _refused_step(
    sig: Signal,
    *,
    reasoner_multiplier: float | None,
    reasoner_halt: bool,
    final_confidence: float,
    refusal_reason: RefusalReason,
    refusal_detail: str,
    risk_decision_reason: str | None = None,
) -> ExecutionStep:
    """Build an ``ExecutionStep`` for a refused signal."""
    return ExecutionStep(
        symbol=sig.symbol,
        side=sig.side,
        strategy=sig.strategy_tag,
        rule_confidence=float(sig.confidence),
        reasoner_multiplier=reasoner_multiplier,
        reasoner_halt=reasoner_halt,
        final_confidence=final_confidence,
        risk_decision_reason=risk_decision_reason,
        submitted=False,
        submission=None,
        refusal_reason=refusal_reason,
        refusal_detail=refusal_detail,
    )
