"""Crypto smart-money wallet ingestion + acceptance gating.

Pulls recent transactions from curated "smart money" wallet lists (Nansen
labels by default) and applies a strict acceptance test before a wallet is
allowed to influence our shadow-copy book. The acceptance criteria come from
the strategy plan and are deliberately conservative — most candidate wallets
should fail.

Default path: Nansen Smart Money if ``NANSEN_API_KEY`` is set in env or
passed explicitly. If neither is provided, the fetch returns an empty list
gracefully (no crash) — crypto alt-data is optional per project defaults.

Tests must NOT hit the network. The single seam is ``_fetch_url``.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

logger = logging.getLogger(__name__)

NANSEN_BASE_URL = "https://api.nansen.ai/api/beta/smart-money"
_REQUEST_TIMEOUT = 30  # seconds
DEFAULT_CHAINS = ("ethereum", "solana", "base")


@dataclass(frozen=True, slots=True)
class WalletTrade:
    """A single observed trade from a tracked smart-money wallet."""

    wallet_address: str
    label: str  # e.g. "Nansen smart money", or custom curated label
    ticker: str
    side: Literal["buy", "sell"]
    txn_time: datetime
    observed_price: float
    our_simulated_fill: float | None  # populated by shadow_copy after this lookup
    our_simulated_slippage_bps: float | None


@dataclass(frozen=True, slots=True)
class WalletAcceptance:
    """Result of running ``evaluate_wallet`` against a wallet's history."""

    address: str
    label: str
    n_trades: int
    n_unique_tokens: int
    pnl_concentration: float  # fraction of P&L from single most profitable token
    avg_slippage_bps: float
    persistence_30d: bool
    persistence_90d: bool
    persistence_180d: bool
    accepted: bool


# ----------------------------------------------------------------------------
# Network seam (kept tiny so tests can monkeypatch in one place).
# ----------------------------------------------------------------------------


def _fetch_url(url: str, api_key: str) -> bytes:
    """Fetch a URL with the Nansen API-key header.

    Tests monkeypatch this function to avoid network calls.
    """
    req = urllib.request.Request(  # noqa: S310 — Nansen https only
        url,
        headers={
            "apiKey": api_key,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:  # noqa: S310
        return resp.read()


# ----------------------------------------------------------------------------
# Parsing.
# ----------------------------------------------------------------------------


def _parse_dt(s: str) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    # Accept Unix epoch seconds as a string too (Nansen sometimes does this).
    if s.replace(".", "", 1).isdigit():
        try:
            return datetime.fromtimestamp(float(s), tz=UTC)
        except (ValueError, OSError, OverflowError):
            return None
    # ISO 8601 with optional trailing Z.
    s_iso = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s_iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _normalize_side(raw: str) -> Literal["buy", "sell"] | None:
    s = (raw or "").strip().lower()
    if s in {"buy", "b", "long", "in", "swap_in"}:
        return "buy"
    if s in {"sell", "s", "short", "out", "swap_out"}:
        return "sell"
    return None


def _to_float(s: str | float | int | None) -> float | None:
    if s is None or s == "":
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _parse_trade(record: dict, default_label: str) -> WalletTrade | None:
    """Convert one Nansen record into a WalletTrade. Returns ``None`` on bad data."""
    wallet = str(
        record.get("address", "") or record.get("wallet", "") or ""
    ).strip()
    ticker = str(
        record.get("symbol", "") or record.get("ticker", "") or record.get("token", "")
    ).upper().strip()
    if not wallet or not ticker:
        return None

    side = _normalize_side(
        str(record.get("side", "") or record.get("type", "") or record.get("direction", ""))
    )
    if side is None:
        return None

    txn_time = _parse_dt(
        str(record.get("blockTimestamp", "") or record.get("txn_time", "") or record.get("ts", ""))
    )
    if txn_time is None:
        return None

    price = _to_float(
        record.get("priceUsd") or record.get("price") or record.get("observed_price")
    )
    if price is None:
        return None

    label = str(record.get("label") or record.get("smartMoneyLabel") or default_label)

    return WalletTrade(
        wallet_address=wallet,
        label=label,
        ticker=ticker,
        side=side,
        txn_time=txn_time,
        observed_price=price,
        our_simulated_fill=None,
        our_simulated_slippage_bps=None,
    )


# ----------------------------------------------------------------------------
# Fetch.
# ----------------------------------------------------------------------------


def fetch_smart_money_trades(
    chains: list[str] | None = None,
    hours: int = 168,
    api_key: str | None = None,
) -> list[WalletTrade]:
    """Pull recent smart-money transactions across one or more chains.

    Default path: Nansen Smart Money if ``api_key`` provided OR
    ``NANSEN_API_KEY`` env var is set.

    Endpoint: ``GET https://api.nansen.ai/api/beta/smart-money/{chain}/transactions``

    Parameters
    ----------
    chains:
        e.g. ``["ethereum", "solana"]``. Defaults to ``DEFAULT_CHAINS``.
    hours:
        Maximum age of returned trades. Defaults to 168 (one week).
    api_key:
        Explicit Nansen key. Falls back to ``NANSEN_API_KEY`` env var.

    Returns
    -------
    list[WalletTrade]
        Empty list if no API key is available (graceful degradation — never
        crashes), if the network call fails, or if no trades are returned.
    """
    key = api_key or os.environ.get("NANSEN_API_KEY") or ""
    if not key:
        # Gated alt-data: silently degrade per project default.
        return []

    chain_list = list(chains) if chains else list(DEFAULT_CHAINS)
    cutoff = datetime.now(tz=UTC) - timedelta(hours=hours)
    out: list[WalletTrade] = []

    for chain in chain_list:
        if not chain:
            continue
        url = (
            f"{NANSEN_BASE_URL}/{urllib.parse.quote(chain)}/transactions"
            f"?hours={hours}"
        )
        try:
            body = _fetch_url(url, api_key=key)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("Nansen fetch failed for %s: %s", chain, exc)
            continue

        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as exc:
            logger.warning("Nansen parse failed for %s: %s", chain, exc)
            continue

        # Nansen returns either a bare list or an object wrapping ``data``.
        records: list = []
        if isinstance(payload, list):
            records = payload
        elif isinstance(payload, dict):
            data = payload.get("data") or payload.get("transactions") or []
            if isinstance(data, list):
                records = data

        for record in records:
            if not isinstance(record, dict):
                continue
            trade = _parse_trade(record, default_label=f"Nansen smart money ({chain})")
            if trade is None:
                continue
            if trade.txn_time < cutoff:
                continue
            out.append(trade)

    return out


# ----------------------------------------------------------------------------
# Acceptance evaluation.
# ----------------------------------------------------------------------------


def _persistence_positive(
    trades: list[WalletTrade],
    pnl_lookup: Callable[[str, datetime], float],
    asof: datetime,
    days: int,
) -> bool:
    """Return True iff cumulative P&L over the last ``days`` is positive.

    P&L per trade is computed as: side * (mark_price_at_asof - observed_price)
    (positive for a buy that has appreciated; positive for a sell that has
    fallen). Only trades whose ``txn_time`` falls in ``[asof - days, asof]``
    are counted.
    """
    window_start = asof - timedelta(days=days)
    pnl = 0.0
    saw_any = False
    for t in trades:
        if t.txn_time < window_start or t.txn_time > asof:
            continue
        try:
            mark = float(pnl_lookup(t.ticker, asof))
        except Exception as exc:
            logger.warning(
                "pnl_lookup failed for %s @ %s: %s", t.ticker, asof, exc
            )
            continue
        sign = 1.0 if t.side == "buy" else -1.0
        pnl += sign * (mark - t.observed_price)
        saw_any = True
    # If we have no trades in the window, treat as non-persistent (False) —
    # the wallet isn't *consistently* active over that horizon.
    return saw_any and pnl > 0.0


def evaluate_wallet(
    address: str,
    history: list[WalletTrade],
    pnl_lookup: Callable[[str, datetime], float],
    min_trades: int = 50,
    max_concentration: float = 0.40,
    max_avg_slippage_bps: float = 25.0,
) -> WalletAcceptance:
    """Apply acceptance criteria from the plan.

    A wallet is **accepted** only if ALL of the following hold:
    * ``len(history) >= min_trades`` (default 50)
    * No single token's P&L > ``max_concentration`` of total |P&L| (default 40%)
    * Mean ``our_simulated_slippage_bps`` < ``max_avg_slippage_bps`` (default 25)
    * Cumulative P&L is positive over each of the trailing 30/90/180 day windows.

    Parameters
    ----------
    address:
        Wallet address being evaluated. Used only to populate the result.
    history:
        All known trades for this wallet (caller is responsible for filtering
        to a single wallet — we don't verify ``t.wallet_address == address``).
    pnl_lookup:
        ``callable(ticker, ts) -> price`` used to mark each trade for the
        persistence check. May raise; we catch and treat the trade as if it
        were unmarkable.
    """
    label = history[0].label if history else ""
    n_trades = len(history)

    if n_trades == 0:
        return WalletAcceptance(
            address=address,
            label=label,
            n_trades=0,
            n_unique_tokens=0,
            pnl_concentration=0.0,
            avg_slippage_bps=0.0,
            persistence_30d=False,
            persistence_90d=False,
            persistence_180d=False,
            accepted=False,
        )

    asof = max(t.txn_time for t in history)

    # Per-token signed P&L (using the same convention as persistence).
    per_token: dict[str, float] = {}
    slip_values: list[float] = []
    for t in history:
        try:
            mark = float(pnl_lookup(t.ticker, asof))
        except Exception as exc:
            logger.warning(
                "pnl_lookup failed in evaluate_wallet: %s", exc
            )
            mark = t.observed_price
        sign = 1.0 if t.side == "buy" else -1.0
        per_token[t.ticker] = per_token.get(t.ticker, 0.0) + sign * (
            mark - t.observed_price
        )
        if t.our_simulated_slippage_bps is not None:
            slip_values.append(float(t.our_simulated_slippage_bps))

    n_unique_tokens = len(per_token)
    total_abs = sum(abs(v) for v in per_token.values())
    if total_abs > 0:
        max_abs = max(abs(v) for v in per_token.values())
        concentration = max_abs / total_abs
    else:
        concentration = 0.0

    avg_slip = sum(slip_values) / len(slip_values) if slip_values else 0.0

    persistence_30d = _persistence_positive(history, pnl_lookup, asof, 30)
    persistence_90d = _persistence_positive(history, pnl_lookup, asof, 90)
    persistence_180d = _persistence_positive(history, pnl_lookup, asof, 180)

    accepted = (
        n_trades >= min_trades
        and concentration <= max_concentration
        and avg_slip < max_avg_slippage_bps
        and persistence_30d
        and persistence_90d
        and persistence_180d
    )

    return WalletAcceptance(
        address=address,
        label=label,
        n_trades=n_trades,
        n_unique_tokens=n_unique_tokens,
        pnl_concentration=concentration,
        avg_slippage_bps=avg_slip,
        persistence_30d=persistence_30d,
        persistence_90d=persistence_90d,
        persistence_180d=persistence_180d,
        accepted=accepted,
    )


__all__ = [
    "WalletAcceptance",
    "WalletTrade",
    "evaluate_wallet",
    "fetch_smart_money_trades",
]
