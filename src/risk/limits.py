"""Trade-level risk gate. `check_limits()` is the canonical risk-manager entry point.

`compliance_check_option()` is the analogous entry point for option orders;
see ``src/risk/option_limits.py`` for the underlying caps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

from src.config import get_settings
from src.execution.option_order import OptionOrder
from src.risk.option_limits import OptionLimitError, OptionLimits, check_option_order
from src.risk.sizing import drawdown_fraction, portfolio_heat, position_size


@dataclass(frozen=True, slots=True)
class Decision:
    approve: bool
    reason: str
    adjusted_size: int | None = None


@dataclass(frozen=True, slots=True)
class ProposedTrade:
    symbol: str
    side: Literal["buy", "sell"]
    entry: Decimal
    stop: Decimal
    target: Decimal | None = None
    strategy_tag: str = ""


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    equity: Decimal
    cash: Decimal
    realized_pnl_today: Decimal
    unrealized_pnl_today: Decimal
    trailing_peak_equity: Decimal
    open_positions: tuple = field(default_factory=tuple)


def check_limits(proposed: ProposedTrade, snapshot: PortfolioSnapshot) -> Decision:
    """Apply all v1 risk caps. Fail closed on any violation."""
    s = get_settings()

    if proposed.side == "buy" and proposed.stop >= proposed.entry:
        return Decision(False, "buy stop must be below entry")
    if proposed.side == "sell" and proposed.stop <= proposed.entry:
        return Decision(False, "sell stop must be above entry")

    dd = drawdown_fraction(snapshot.equity, snapshot.trailing_peak_equity)
    if dd >= s.DRAWDOWN_HALT:
        return Decision(
            False,
            f"drawdown {dd:.2%} >= {s.DRAWDOWN_HALT:.2%} halt — manual reset required",
        )

    intraday_pnl = snapshot.realized_pnl_today + snapshot.unrealized_pnl_today
    if snapshot.equity > 0:
        intraday_pct = intraday_pnl / snapshot.equity
        if intraday_pct <= s.DAILY_LOSS_HALT:
            return Decision(
                False,
                f"intraday P&L {intraday_pct:.2%} <= {s.DAILY_LOSS_HALT:.2%} — halted",
            )

    qty = position_size(
        equity=snapshot.equity,
        risk_pct=s.MAX_PER_TRADE_RISK,
        entry=proposed.entry,
        stop=proposed.stop,
        max_position_pct=s.MAX_SINGLE_POSITION,
    )
    if qty <= 0:
        return Decision(False, "computed size <= 0 (stop too wide vs. risk cap)")

    notional = qty * proposed.entry
    if notional > snapshot.equity * s.MAX_SINGLE_POSITION:
        return Decision(
            False,
            f"notional {notional} > {s.MAX_SINGLE_POSITION:.0%} of equity",
        )

    new_risk = qty * abs(proposed.entry - proposed.stop)
    existing_heat = portfolio_heat(snapshot.open_positions, snapshot.equity)
    add_heat = new_risk / snapshot.equity
    if existing_heat + add_heat > s.MAX_PORTFOLIO_HEAT:
        return Decision(
            False,
            f"portfolio heat {(existing_heat + add_heat):.2%} > {s.MAX_PORTFOLIO_HEAT:.2%}",
        )

    return Decision(True, "all caps OK", adjusted_size=qty)


def compliance_check_option(
    order: OptionOrder,
    equity: Decimal,
    open_csp_contracts: int,
    today: date | None = None,
    limits: OptionLimits | None = None,
) -> Decision:
    """Compliance gate for option orders.

    v1 policy (see ``docs/policy.md`` §6 "Account type"): the only allowed
    option strategies are covered_call, cash_secured_put, and protective_put.
    Naked options are rejected at :class:`OptionOrder` construction; this gate
    additionally enforces the quantitative caps in
    :func:`src.risk.option_limits.check_option_order`.

    Returns an APPROVE :class:`Decision` on pass, REJECT with a populated
    ``reason`` on any cap violation. The caller passes the ``Decision`` to
    :func:`src.execution.broker.approval_token` exactly like the equity path.
    """
    today = today or date.today()
    try:
        check_option_order(
            order=order,
            equity=equity,
            open_csp_contracts=open_csp_contracts,
            today=today,
            limits=limits,
        )
    except OptionLimitError as exc:
        return Decision(False, f"option compliance rejected: {exc}")

    return Decision(
        True,
        f"option compliance OK ({order.strategy_kind}, max_loss={order.max_loss_usd})",
    )
