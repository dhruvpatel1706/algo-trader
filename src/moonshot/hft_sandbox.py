"""HFT lane: paper-only sandbox with simulated fills + latency telemetry.

This module is a permanent research lane. It NEVER bridges to a real broker —
there is no broker import, no order-router callable, no network egress path.
Its purpose is to research latency-sensitive ideas (queue position, micro
mean-reversion) on stored intraday data without ANY chance of leaking to live
execution.
"""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

# Module-level invariant mirror — see `tests/unit/moonshot/test_bridge_invariant.py`.
LIVE_BROKER_BRIDGE: bool = False


@dataclass(frozen=True, slots=True)
class HftFill:
    symbol: str
    side: Literal["buy", "sell"]
    qty: int
    intended_price: float
    simulated_price: float
    latency_us: int  # microseconds
    queue_position_estimate: float  # 0..1, fraction of book ahead of us
    ts: datetime


class HftSandbox:
    """Simulated-fill sandbox with latency telemetry. NEVER bridges to a real broker.

    Purpose: research latency-sensitive ideas (queue position, micro-mean-reversion)
    on stored intraday data without ANY chance of leaking to live execution.
    """

    # Explicit safety flag. Asserted by tests; NEVER mutate to True.
    LIVE_BROKER_BRIDGE: bool = False

    def __init__(self, latency_budget_us: int = 5000, seed: int | None = None) -> None:
        self._latency_budget = latency_budget_us
        self._fills: list[HftFill] = []
        self._rng = random.Random(seed)

    @property
    def fills(self) -> list[HftFill]:
        return list(self._fills)

    def _draw_latency_us(self) -> int:
        """Pareto(alpha=2) draw scaled to budget, capped at 10x the budget."""
        # inverse CDF: x = scale / U^(1/alpha); we treat scale as latency_budget/2.
        u = self._rng.random()
        # Avoid u=0 → infinity.
        u = max(u, 1e-9)
        alpha = 2.0
        sample = (self._latency_budget * 0.5) / (u ** (1.0 / alpha))
        capped = min(sample, self._latency_budget * 10.0)
        return int(max(1, capped))

    def _slippage_bps(
        self, side: Literal["buy", "sell"], queue_position: float, vol: float
    ) -> float:
        """Crude slippage model: deeper in queue → more adverse selection.

        bps = 1 + 5 * queue_position + 50 * vol  (volatility expressed as fraction).
        Sign: buys slip up, sells slip down.
        """
        bps = 1.0 + 5.0 * queue_position + 50.0 * vol
        return bps if side == "buy" else -bps

    def _estimate_queue_position(self, order: Any, market_book: dict) -> float:
        """Order's queue position as fraction of displayed liquidity ahead of us."""
        displayed = float(market_book.get("displayed_liquidity", 0.0) or 0.0)
        if displayed <= 0:
            return 0.0
        qty = float(getattr(order, "qty", 0) or 0)
        # Larger orders relative to displayed liquidity → further back.
        frac = min(1.0, qty / displayed)
        return frac

    def submit(self, order: Any, market_book: dict) -> HftFill:
        """Apply realistic fill model.

        - latency_us drawn from Pareto(alpha=2) capped at 10x budget
        - simulated_price = intended_price * (1 + slippage_bps / 10_000)
        - queue_position = order qty / displayed liquidity (clipped to 1.0)

        SAFETY: this function does NOT touch any broker / network path. It only
        appends to an in-memory list of HftFill records.
        """
        if self.LIVE_BROKER_BRIDGE:  # pragma: no cover - safety guard
            raise RuntimeError("HftSandbox MUST never bridge to a live broker.")

        latency_us = self._draw_latency_us()
        queue_position = self._estimate_queue_position(order, market_book)
        vol = float(market_book.get("vol", 0.0) or 0.0)
        side: Literal["buy", "sell"] = getattr(order, "side", "buy")
        intended = float(getattr(order, "price", 0.0) or 0.0)
        bps = self._slippage_bps(side, queue_position, vol)
        simulated_price = intended * (1.0 + bps / 10_000.0)

        fill = HftFill(
            symbol=getattr(order, "symbol", ""),
            side=side,
            qty=int(getattr(order, "qty", 0) or 0),
            intended_price=intended,
            simulated_price=simulated_price,
            latency_us=latency_us,
            queue_position_estimate=queue_position,
            ts=getattr(order, "ts", datetime.now(UTC)),
        )
        self._fills.append(fill)
        return fill

    def stats(self) -> dict:
        """Latency p50/p95/p99, fill ratio, slippage by symbol."""
        if not self._fills:
            return {
                "count": 0,
                "latency_p50_us": 0,
                "latency_p95_us": 0,
                "latency_p99_us": 0,
                "fill_ratio": 0.0,
                "slippage_bps_by_symbol": {},
            }
        latencies = sorted(f.latency_us for f in self._fills)

        def _pct(p: float) -> int:
            idx = max(0, min(len(latencies) - 1, round(p * (len(latencies) - 1))))
            return latencies[idx]

        # Sandbox always fills (no rejects in this model). Fill ratio is 1.0.
        fill_ratio = 1.0

        slippage_by_symbol: dict[str, float] = {}
        counts: dict[str, int] = {}
        for f in self._fills:
            if f.intended_price <= 0:
                continue
            bps = (f.simulated_price - f.intended_price) / f.intended_price * 10_000.0
            slippage_by_symbol.setdefault(f.symbol, 0.0)
            counts.setdefault(f.symbol, 0)
            slippage_by_symbol[f.symbol] += bps
            counts[f.symbol] += 1
        for sym in slippage_by_symbol:
            slippage_by_symbol[sym] /= counts[sym]

        return {
            "count": len(self._fills),
            "latency_p50_us": _pct(0.50),
            "latency_p95_us": _pct(0.95),
            "latency_p99_us": _pct(0.99),
            "latency_mean_us": int(statistics.mean(latencies)),
            "fill_ratio": fill_ratio,
            "slippage_bps_by_symbol": slippage_by_symbol,
        }
