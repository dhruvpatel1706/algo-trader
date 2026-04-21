"""Position sizing and risk math. Pure functions; money in `Decimal`."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import ROUND_DOWN, Decimal
from typing import Protocol

# Minimum stop distance to avoid div-by-zero (one cent for stocks).
_EPS = Decimal("0.01")


class _PositionLike(Protocol):
    open_risk: Decimal


def position_size(
    equity: Decimal,
    risk_pct: Decimal,
    entry: Decimal,
    stop: Decimal,
    *,
    max_position_pct: Decimal | None = None,
) -> int:
    """Integer share count sized to risk `risk_pct` of `equity` to a `stop`.

    qty = floor((equity * risk_pct) / max(|entry - stop|, EPS))
    Capped to floor((equity * max_position_pct) / entry) if provided.
    """
    if equity <= 0:
        raise ValueError("equity must be positive")
    if not (Decimal("0") < risk_pct <= Decimal("1")):
        raise ValueError("risk_pct must be in (0, 1]")
    if entry <= 0:
        raise ValueError("entry must be positive")
    if stop <= 0:
        raise ValueError("stop must be positive")

    risk_per_share = max(abs(entry - stop), _EPS)
    raw = (equity * risk_pct) / risk_per_share
    qty = int(raw.to_integral_value(rounding=ROUND_DOWN))

    if max_position_pct is not None:
        cap = int(((equity * max_position_pct) / entry).to_integral_value(rounding=ROUND_DOWN))
        qty = min(qty, cap)

    return max(qty, 0)


def kelly_fraction(win_rate: float, win_loss_ratio: float) -> float:
    """Full Kelly. Use sparingly — prefer `quarter_kelly()`. Clipped at 0."""
    if not (0.0 <= win_rate <= 1.0):
        raise ValueError("win_rate must be in [0, 1]")
    if win_loss_ratio <= 0:
        raise ValueError("win_loss_ratio must be positive")
    f = win_rate - (1 - win_rate) / win_loss_ratio
    return max(f, 0.0)


def quarter_kelly(win_rate: float, win_loss_ratio: float) -> float:
    """1/4 Kelly — conservative practical sizing for noisy edges."""
    return kelly_fraction(win_rate, win_loss_ratio) / 4


def portfolio_heat(positions: Iterable[_PositionLike], equity: Decimal) -> Decimal:
    """Sum of open dollar risk divided by equity. 0.06 means 6% of equity at risk."""
    if equity <= 0:
        raise ValueError("equity must be positive")
    total = sum((p.open_risk for p in positions), Decimal("0"))
    return total / equity


def drawdown_fraction(current_equity: Decimal, trailing_peak: Decimal) -> Decimal:
    """Drawdown as a positive fraction. 0.20 means 20% off peak. Negative DD clamps to 0."""
    if trailing_peak <= 0:
        raise ValueError("trailing_peak must be positive")
    if current_equity > trailing_peak:
        return Decimal("0")
    return (trailing_peak - current_equity) / trailing_peak
