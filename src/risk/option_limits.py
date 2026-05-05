"""Per-order option risk caps, layered on top of the equity caps in ``limits.py``.

These caps run after :class:`OptionOrder` validates structure ("no naked
options") — they enforce *quantitative* constraints: DTE windows, total CSP
collateral as a fraction of equity, max simultaneous CSP contracts, minimum
yield, and a hard "max_loss_usd must be ≤ 1% of equity" check that piggybacks
on the existing per-trade risk cap.

Failures raise :class:`OptionLimitError` with a human-readable reason. Pass =
returns ``None`` (matches the project's other "fail-closed validator" idiom).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from src.config import get_settings
from src.execution.option_order import CONTRACT_MULTIPLIER, OptionOrder


@dataclass(frozen=True, slots=True)
class OptionLimits:
    """v1 caps for options. Stricter than the equity caps because options
    have asymmetric, time-decaying payoffs."""

    # Total CSP cash collateral as % of equity. 30% keeps the CSP book from
    # eating the whole portfolio if vol spikes and assignment looks likely.
    max_csp_collateral_pct: Decimal = Decimal("0.30")
    # Max # of CSP contracts open at once. Caps notional concurrent exposure
    # independent of dollar-collateral cap.
    max_csp_open_contracts: int = 5
    # Min DTE — too-short DTE is gamma-roulette.
    min_dte: int = 21
    # Max DTE — too-long DTE ties up capital with low theta.
    max_dte: int = 60
    # Min target premium / collateral ratio. 0.5% keeps us out of "selling
    # vol for free" territory in compressed-IV regimes.
    min_premium_yield: Decimal = Decimal("0.005")


class OptionLimitError(Exception):
    """Raised when an OptionOrder violates v1 option limits."""


def _csp_collateral_per_contract(order: OptionOrder) -> Decimal:
    """Cash collateral required per CSP contract = strike * 100."""
    leg = order.legs[0]
    return leg.contract.strike * Decimal(CONTRACT_MULTIPLIER)


def _csp_total_collateral(order: OptionOrder) -> Decimal:
    """Total cash collateral for the (possibly multi-contract) CSP order."""
    leg = order.legs[0]
    return _csp_collateral_per_contract(order) * Decimal(leg.quantity)


def check_option_order(
    order: OptionOrder,
    equity: Decimal,
    open_csp_contracts: int,
    today: date,
    limits: OptionLimits | None = None,
) -> None:
    """Hard-validate an option order against v1 option caps.

    Raises :class:`OptionLimitError` on the first failure. Returns ``None`` on pass.

    Checks (in evaluation order):
      1. ``max_loss_usd`` is bounded (defense-in-depth — also enforced in
         :class:`OptionOrder`).
      2. DTE within ``[min_dte, max_dte]``.
      3. ``max_loss_usd`` ≤ ``MAX_PER_TRADE_RISK * equity`` (1% by default).
      4. For CSPs only:
         - count(open + this) ≤ ``max_csp_open_contracts``
         - total CSP collateral (this order; we don't know past collateral) ≤
           ``max_csp_collateral_pct * equity``
         - per-contract premium / collateral ≥ ``min_premium_yield``
    """
    if equity <= 0:
        raise OptionLimitError(f"equity must be positive (got {equity})")

    cfg = limits or OptionLimits()
    settings = get_settings()

    # 1. defense-in-depth: max_loss_usd must be bounded
    max_loss = order.max_loss_usd
    if max_loss is None:
        raise OptionLimitError("max_loss_usd is unbounded — naked options are forbidden in v1")

    # 2. DTE window
    dte = (order.legs[0].contract.expiration - today).days
    if dte < cfg.min_dte:
        raise OptionLimitError(
            f"DTE {dte} below minimum {cfg.min_dte} (gamma risk too high)"
        )
    if dte > cfg.max_dte:
        raise OptionLimitError(
            f"DTE {dte} above maximum {cfg.max_dte} (capital tied up too long)"
        )

    # 3. Per-trade risk cap (re-uses the equity per-trade cap from settings).
    per_trade_cap = equity * settings.MAX_PER_TRADE_RISK
    if max_loss > per_trade_cap:
        raise OptionLimitError(
            f"max_loss_usd {max_loss} > per-trade cap {per_trade_cap} "
            f"({settings.MAX_PER_TRADE_RISK:.2%} of equity)"
        )

    # 4. CSP-specific caps
    if order.strategy_kind == "cash_secured_put":
        leg = order.legs[0]
        new_contracts = leg.quantity
        if open_csp_contracts + new_contracts > cfg.max_csp_open_contracts:
            raise OptionLimitError(
                f"CSP contract count {open_csp_contracts + new_contracts} > "
                f"{cfg.max_csp_open_contracts}"
            )

        total_collateral = _csp_total_collateral(order)
        collateral_cap = equity * cfg.max_csp_collateral_pct
        if total_collateral > collateral_cap:
            raise OptionLimitError(
                f"CSP collateral {total_collateral} > cap {collateral_cap} "
                f"({cfg.max_csp_collateral_pct:.0%} of equity)"
            )

        # Premium yield: premium per contract / collateral per contract.
        # Without a limit_price, we treat premium as 0 and reject — a CSP
        # with no premium is not a trade.
        premium_per_contract = (
            leg.limit_price * Decimal(CONTRACT_MULTIPLIER)
            if leg.limit_price is not None
            else Decimal("0")
        )
        collateral_per_contract = _csp_collateral_per_contract(order)
        yield_ratio = (
            premium_per_contract / collateral_per_contract
            if collateral_per_contract > 0
            else Decimal("0")
        )
        if yield_ratio < cfg.min_premium_yield:
            raise OptionLimitError(
                f"CSP yield {yield_ratio:.4f} < min {cfg.min_premium_yield:.4f}"
            )
