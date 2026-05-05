"""Copy-trading lane: shadow-copy first.

Record source trade, simulate our fill, compare 30/90/180-day P&L. This lane is
a research tool only — it never sends an order to a real broker.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal

# Lane safety flag for tests.
LIVE_BROKER_BRIDGE: bool = False


@dataclass(frozen=True, slots=True)
class ShadowTrade:
    source_id: str  # politician name, insider name, wallet address
    source_label: str  # e.g. "Rep. X (D-CA)", "CFO of XYZ", "Nansen smart money"
    ticker: str
    side: Literal["buy", "sell"]
    source_qty: int | None
    source_price: float
    source_ts: datetime
    our_simulated_price: float = 0.0  # achievable price after slippage/spread
    our_simulated_qty: int = 0  # what we could realistically have done
    delay_seconds: float = 0.0  # source -> our observation
    note: str = ""


# A small list of "majors" with tighter slippage. Anything else gets the wider
# slippage assumption.
_MAJORS = frozenset(
    {
        "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NVDA", "TSLA",
        "JPM", "BAC", "WMT", "XOM", "JNJ", "V", "PG", "HD", "MA", "UNH",
        "SPY", "QQQ", "IWM", "DIA", "VTI", "VOO",
    }
)


def _default_slippage_bps(ticker: str) -> float:
    return 25.0 if ticker.upper() in _MAJORS else 100.0


def simulate_our_fill(
    source_trade: ShadowTrade,
    our_capital: float,
    book_depth_callback: Callable[[str, datetime], dict] | None = None,
) -> ShadowTrade:
    """Simulate our achievable fill given current market state.

    Default if no book_depth_callback: use 25 bps slippage for majors, 100 bps
    for less liquid, return updated ShadowTrade with our_simulated_*.
    """
    ticker = source_trade.ticker
    side = source_trade.side
    source_price = source_trade.source_price

    if book_depth_callback is not None:
        try:
            book = book_depth_callback(ticker, source_trade.source_ts)
        except Exception:
            book = {}
        slippage_bps = float(book.get("slippage_bps", _default_slippage_bps(ticker)))
        spread_bps = float(book.get("spread_bps", 0.0))
        bps = slippage_bps + spread_bps
    else:
        bps = _default_slippage_bps(ticker)

    sign = 1.0 if side == "buy" else -1.0
    our_simulated_price = source_price * (1.0 + sign * bps / 10_000.0)

    # Cap our quantity by available capital (long-only sizing for shadow lane).
    if our_simulated_price <= 0:
        our_qty = 0
    elif side == "buy":
        our_qty = int(our_capital // our_simulated_price)
    else:
        # For sells we mirror the source size, capped by capital sanity.
        our_qty = int(source_trade.source_qty or 0)
        cap = int(our_capital // max(our_simulated_price, 1e-9))
        if our_qty == 0 or cap < our_qty:
            our_qty = cap

    return replace(
        source_trade,
        our_simulated_price=float(our_simulated_price),
        our_simulated_qty=int(max(0, our_qty)),
    )


def _pnl(side: Literal["buy", "sell"], entry: float, exit_: float, qty: int) -> float:
    if qty <= 0 or entry <= 0 or exit_ <= 0:
        return 0.0
    sign = 1.0 if side == "buy" else -1.0
    return sign * (exit_ - entry) * qty


def compare_pnl(
    shadow_trades: list[ShadowTrade],
    horizon_days: int,
    asof_pricer: Callable[[str, datetime], float],
) -> dict:
    """For each ShadowTrade, compute source P&L vs our P&L over horizon.

    Return aggregate: source_total_pnl, our_total_pnl, ratio, count.
    Acceptance: our_total_pnl >= 0.6 * source_total_pnl with at least 50 trades.
    """
    if horizon_days < 0:
        raise ValueError("horizon_days must be non-negative")

    source_total = 0.0
    our_total = 0.0
    count = 0
    for tr in shadow_trades:
        exit_ts = tr.source_ts + timedelta(days=horizon_days)
        try:
            exit_price = float(asof_pricer(tr.ticker, exit_ts))
        except Exception:
            logging.getLogger(__name__).debug(
                "copy_shadow.exit_price_unavailable",
                extra={"ticker": tr.ticker, "ts": str(exit_ts)},
            )
            continue
        source_qty = int(tr.source_qty or 0)
        source_total += _pnl(tr.side, tr.source_price, exit_price, source_qty)
        our_total += _pnl(
            tr.side, tr.our_simulated_price, exit_price, tr.our_simulated_qty
        )
        count += 1

    if source_total == 0.0:
        ratio: float = 0.0 if our_total == 0.0 else float("inf")
    else:
        ratio = our_total / source_total

    accepted = (count >= 50) and (our_total >= 0.6 * source_total) if source_total > 0 else False

    return {
        "count": count,
        "horizon_days": horizon_days,
        "source_total_pnl": source_total,
        "our_total_pnl": our_total,
        "ratio": ratio,
        "accepted": accepted,
    }
