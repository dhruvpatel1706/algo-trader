"""Post-mark concentration monitor.

The per-symbol cap in :mod:`src.risk.limits` (default 10% of equity) catches
new-entry violations: it refuses any submit that would cumulatively push a
symbol past the cap. What it does NOT catch is *drift* — a position bought at
9% that appreciates (or accumulates via partial fills) to 30% sits inside an
already-approved stack, and the cap won't trim it.

This module emits an alarm when that happens. It does not place orders, does
not modify state, does not halt strategies — that's the operator's call.

Why 30% (not 10%) is the alarm threshold:
  - The 10% per-symbol cap is *prevention*: every fresh add gets gated.
  - The 30% alarm is *visibility*: if drift has tripled the position, the
    operator should know explicitly even if no rule was violated. 30% matches
    the watcher's existing alarm threshold so the dashboard and the orchestrator
    agree on what counts as concerning.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ConcentrationBreach:
    """One symbol whose mark-value share of equity exceeds the alarm threshold."""

    symbol: str
    notional: Decimal
    equity: Decimal
    fraction: float
    threshold: float


def compute_position_concentrations(
    open_positions: object, equity: Decimal
) -> dict[str, float]:
    """Return ``{symbol: notional/equity}`` for every held position.

    ``open_positions`` accepts the same shape :func:`check_limits` consumes
    (adapter instances with ``.symbol`` / ``.notional`` or raw broker dicts
    with ``market_value`` / ``qty * avg_entry_price``).

    Returns an empty dict on missing/zero equity rather than raising — a
    snapshot read failure must never break the trading cycle.
    """
    if not open_positions or equity <= 0:
        return {}

    out: dict[str, float] = {}
    for p in open_positions:
        sym, notional = _extract(p)
        if not sym:
            continue
        out[sym] = float(abs(notional) / equity)
    return out


def concentration_breaches(
    open_positions: object,
    equity: Decimal,
    *,
    threshold: float = 0.30,
) -> list[ConcentrationBreach]:
    """Return one :class:`ConcentrationBreach` per symbol over ``threshold``.

    Empty list when the book is flat, equity is zero, or every position is
    within bounds.
    """
    if not open_positions or equity <= 0 or threshold <= 0:
        return []

    breaches: list[ConcentrationBreach] = []
    for p in open_positions:
        sym, notional = _extract(p)
        if not sym:
            continue
        notional_abs = abs(notional)
        frac = float(notional_abs / equity)
        if frac > threshold:
            breaches.append(
                ConcentrationBreach(
                    symbol=sym,
                    notional=notional_abs,
                    equity=equity,
                    fraction=frac,
                    threshold=threshold,
                )
            )
    return breaches


def _extract(p: object) -> tuple[str, Decimal]:
    """Pull (symbol, notional) from either an adapter or a broker dict."""
    if hasattr(p, "symbol") and hasattr(p, "notional"):
        sym = str(getattr(p, "symbol", "") or "")
        try:
            return sym, Decimal(str(getattr(p, "notional", 0) or 0))
        except (ArithmeticError, TypeError, ValueError):
            return sym, Decimal("0")
    if isinstance(p, dict):
        sym = str(p.get("symbol", "") or "")
        mv = p.get("market_value")
        if mv is None:
            try:
                qty = Decimal(str(p.get("qty", 0)))
                avg = Decimal(str(p.get("avg_entry_price", 0)))
                return sym, qty * avg
            except (ArithmeticError, TypeError, ValueError):
                return sym, Decimal("0")
        try:
            return sym, Decimal(str(mv))
        except (ArithmeticError, TypeError, ValueError):
            return sym, Decimal("0")
    return "", Decimal("0")
