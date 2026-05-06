"""Cached read access to Alpaca paper account state.

Reads are cached for ~3s to avoid rate limiting under dashboard polling.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.config import get_settings

log = logging.getLogger(__name__)


@dataclass
class _CacheEntry:
    value: Any
    fetched_at: float


class BrokerProxy:
    """Thin facade over alpaca-py TradingClient with 3s read cache."""

    def __init__(self, ttl: float = 3.0) -> None:
        self._ttl = ttl
        self._cache: dict[str, _CacheEntry] = {}
        self._client: Any = None

    def _get_client(self):
        if self._client is None:
            s = get_settings()
            if not (s.ALPACA_API_KEY and s.ALPACA_SECRET_KEY):
                return None
            from alpaca.trading.client import TradingClient

            self._client = TradingClient(s.ALPACA_API_KEY, s.ALPACA_SECRET_KEY, paper=True)
        return self._client

    def _cached(self, key: str, fetcher) -> Any:
        now = time.time()
        entry = self._cache.get(key)
        if entry and (now - entry.fetched_at) < self._ttl:
            return entry.value
        client = self._get_client()
        if client is None:
            return None
        value = fetcher(client)
        self._cache[key] = _CacheEntry(value=value, fetched_at=now)
        return value

    def get_account(self) -> dict | None:
        def fetch(c):
            a = c.get_account()
            return {
                "equity": float(a.equity),
                "cash": float(a.cash),
                "buying_power": float(a.buying_power),
                "portfolio_value": float(a.portfolio_value),
                "last_equity": float(a.last_equity),
                "day_change_usd": float(a.equity) - float(a.last_equity),
                "day_change_pct": (
                    (float(a.equity) - float(a.last_equity)) / float(a.last_equity)
                    if float(a.last_equity)
                    else 0.0
                ),
                "account_blocked": bool(a.account_blocked),
                "trading_blocked": bool(a.trading_blocked),
                "paper": True,
            }

        return self._cached("account", fetch)

    def get_positions(self) -> list[dict] | None:
        def fetch(c):
            pos = c.get_all_positions()
            return [
                {
                    "symbol": p.symbol,
                    # qty stays float — crypto positions hold fractional units.
                    # int() truncation here was silently dropping ~$2K of value
                    # for a typical 4-ETH position (3.99 ETH → 3 ETH).
                    "qty": float(p.qty),
                    "avg_entry_price": float(p.avg_entry_price),
                    "market_value": float(p.market_value),
                    "unrealized_pl": float(p.unrealized_pl),
                    "unrealized_plpc": float(p.unrealized_plpc),
                    "current_price": float(p.current_price),
                    "side": str(p.side).lower(),
                }
                for p in pos
            ]

        return self._cached("positions", fetch)

    def get_orders(self, status: str = "all", limit: int = 50) -> list[dict] | None:
        def fetch(c):
            from alpaca.trading.requests import GetOrdersRequest

            req = GetOrdersRequest(status=status, limit=limit)
            orders = c.get_orders(filter=req)
            return [
                {
                    "id": str(o.id),
                    "client_order_id": o.client_order_id,
                    "symbol": o.symbol,
                    # qty + filled_qty stay float; see get_positions() rationale.
                    "qty": float(o.qty) if o.qty else None,
                    "side": str(o.side).lower(),
                    "type": str(o.order_type).lower(),
                    "limit_price": float(o.limit_price) if o.limit_price else None,
                    "status": str(o.status).lower(),
                    "submitted_at": str(o.submitted_at) if o.submitted_at else None,
                    "filled_qty": float(o.filled_qty) if o.filled_qty else 0.0,
                    "filled_avg_price": (float(o.filled_avg_price) if o.filled_avg_price else None),
                }
                for o in orders
            ]

        return self._cached(f"orders:{status}:{limit}", fetch)

    @staticmethod
    def _period_for_days(days: int) -> str:
        """Map our ``days`` query param to the closest Alpaca ``period`` bucket.

        Alpaca's bucketing is fixed (1D/1W/1M/3M/1A/all); we pick the
        smallest bucket that covers ``days``.
        """
        if days <= 1:
            return "1D"
        if days <= 7:
            return "1W"
        if days <= 30:
            return "1M"
        if days <= 90:
            return "3M"
        if days <= 365:
            return "1A"
        return "all"

    def get_portfolio_history(self, *, days: int = 90) -> list[dict] | None:
        """Fetch Alpaca's server-side equity history.

        Returns a list of {ts, equity} dicts in chronological order, or None
        if the broker is not configured / the call failed (caller should
        fall back to the empty list cleanly).

        The mapping from ``days`` to Alpaca's ``period`` parameter is approximate
        — Alpaca's bucketing is fixed (1D/1W/1M/3M/1A/all); we pick the
        smallest bucket that covers ``days``.
        """
        client = self._get_client()
        if client is None:
            return None
        period = self._period_for_days(days)
        try:
            from alpaca.trading.requests import (
                GetPortfolioHistoryRequest,
            )

            req = GetPortfolioHistoryRequest(period=period, timeframe="1D")
            history = client.get_portfolio_history(history_filter=req)
        except TypeError:
            # Older alpaca-py versions accept positional/keyword args differently.
            try:
                history = client.get_portfolio_history(period=period, timeframe="1D")
            except Exception as exc:  # pragma: no cover - defensive
                log.debug("alpaca get_portfolio_history failed: %s", exc)
                return None
        except Exception as exc:
            log.debug("alpaca get_portfolio_history failed: %s", exc)
            return None

        timestamps = getattr(history, "timestamp", None) or []
        equities = getattr(history, "equity", None) or []
        out: list[dict] = []
        for ts, eq in zip(timestamps, equities, strict=False):
            if eq is None:
                continue
            try:
                eq_f = float(eq)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(eq_f):
                continue
            try:
                ts_iso = datetime.fromtimestamp(int(ts), tz=UTC).isoformat()
            except (TypeError, ValueError, OSError):
                continue
            out.append({"ts": ts_iso, "equity": eq_f})
        return out

    def cancel_all_orders(self) -> list[str] | None:
        client = self._get_client()
        if client is None:
            return None
        cancelled = client.cancel_orders()
        return [str(c.id) for c in cancelled]

    def close_all_positions(self) -> list[str] | None:
        """Liquidate all open positions at market."""
        client = self._get_client()
        if client is None:
            return None
        responses = client.close_all_positions(cancel_orders=True)
        return [str(r.symbol) for r in responses if hasattr(r, "symbol")]


# Module-level singleton.
_proxy: BrokerProxy | None = None


def get_broker_proxy() -> BrokerProxy:
    global _proxy
    if _proxy is None:
        _proxy = BrokerProxy()
    return _proxy
