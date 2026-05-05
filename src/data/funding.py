"""Perpetual-funding data + signal-suppression filter.

Funding rate is the periodic payment between perp longs and shorts to keep the
perp price tethered to spot. Persistently positive funding => longs paying shorts
=> crowd is long. Use that to dampen long signals near extremes (and shorts at
the opposite extreme).

Public Binance endpoint, no authentication. Network paths return an empty frame
on failure so callers can degrade gracefully.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import warnings
from datetime import UTC, date, datetime
from typing import Literal

import pandas as pd

_BINANCE_FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
_REQUEST_TIMEOUT = 10  # seconds
_MAX_LIMIT = 1000


def _to_ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000)


def _normalize_symbol(symbol: str) -> str:
    """Accept 'BTC-USDT', 'BTC/USDT', 'BTCUSDT' -> 'BTCUSDT'."""
    s = symbol.upper().replace("-", "").replace("/", "")
    return s


def fetch_funding_rate(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Pull funding rate history from Binance public endpoint (no auth).

    Endpoint: GET https://fapi.binance.com/fapi/v1/fundingRate

    Parameters
    ----------
    symbol : str
        e.g. "BTCUSDT" (also accepts "BTC-USDT" / "BTC/USDT").
    start, end : date
        UTC date range, inclusive on start, exclusive on end semantics
        (Binance treats startTime/endTime as ms epochs).

    Returns
    -------
    pd.DataFrame
        Indexed by UTC timestamp. Columns: ``funding_rate``, ``predicted_rate``.
        Empty DataFrame on any network/parse failure (and a warning is emitted).
    """
    sym = _normalize_symbol(symbol)
    params = {
        "symbol": sym,
        "startTime": _to_ms(start),
        "endTime": _to_ms(end),
        "limit": _MAX_LIMIT,
    }
    url = f"{_BINANCE_FUNDING_URL}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=_REQUEST_TIMEOUT) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        warnings.warn(
            f"funding rate fetch failed for {sym}: {e!r}; returning empty frame",
            stacklevel=2,
        )
        return _empty_funding_frame()
    except (json.JSONDecodeError, ValueError) as e:
        warnings.warn(
            f"funding rate parse failed for {sym}: {e!r}; returning empty frame",
            stacklevel=2,
        )
        return _empty_funding_frame()

    if not isinstance(payload, list) or not payload:
        return _empty_funding_frame()

    rows = []
    for r in payload:
        ts = pd.to_datetime(int(r.get("fundingTime", 0)), unit="ms", utc=True)
        rate = float(r.get("fundingRate", 0.0))
        # The public endpoint does NOT return predicted_rate; surface it as NaN to
        # keep the column shape stable for downstream consumers.
        rows.append((ts, rate, float("nan")))

    df = pd.DataFrame(rows, columns=["timestamp", "funding_rate", "predicted_rate"])
    df = df.set_index("timestamp").sort_index()
    return df


def _empty_funding_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {"funding_rate": pd.Series(dtype="float64"),
         "predicted_rate": pd.Series(dtype="float64")},
    )


def funding_filter_score(
    symbol: str,
    funding_history: pd.DataFrame,
    asof: date,
    side: Literal["long", "short"],
) -> float:
    """Return a 0..1 multiplier to apply to a candidate signal's confidence.

    Long signals are suppressed when funding is in the top quartile (longs are
    overpaying); short signals are suppressed in the bottom quartile (shorts
    overpaying). Mid-range funding returns 1.0.

    The suppression curve is piecewise linear:
        score = 1.0                    if percentile is in [25, 75]
        score = 1.0 -> 0.5             linear from p75 toward p100 (long side)
        score = 1.0 -> 0.5             linear from p25 toward p0   (short side)

    A heavy-extremity score is clipped at 0.5 so this is a *dampener*, not a hard
    veto. Callers can compose with stricter filters if they need a kill switch.

    Empty / insufficient history returns 1.0 (no penalty) — fail open.
    """
    if funding_history is None or funding_history.empty:
        return 1.0
    if "funding_rate" not in funding_history.columns:
        return 1.0

    asof_ts = pd.Timestamp(asof, tz="UTC")
    history = funding_history.loc[funding_history.index <= asof_ts]
    if history.empty:
        return 1.0

    rates = history["funding_rate"].dropna()
    if rates.empty:
        return 1.0

    current = float(rates.iloc[-1])
    p25, p75 = float(rates.quantile(0.25)), float(rates.quantile(0.75))
    p_min, p_max = float(rates.min()), float(rates.max())

    if side == "long":
        if current <= p75:
            return 1.0
        # Beyond p75: scale down to 0.5 at p_max.
        denom = max(p_max - p75, 1e-12)
        frac = min(1.0, max(0.0, (current - p75) / denom))
        return float(1.0 - 0.5 * frac)

    if side == "short":
        if current >= p25:
            return 1.0
        denom = max(p25 - p_min, 1e-12)
        frac = min(1.0, max(0.0, (p25 - current) / denom))
        return float(1.0 - 0.5 * frac)

    return 1.0


__all__ = ["fetch_funding_rate", "funding_filter_score"]
