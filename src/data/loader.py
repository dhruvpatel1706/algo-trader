"""Market-data loader (Alpaca daily bars + parquet cache, yfinance fallback).

yfinance is for prototyping only. Sizing / risk decisions in production must use
broker-grade data. Loud warning when the fallback fires.

Crypto OHLCV is fetched from public REST endpoints (Binance / Coinbase) using
stdlib urllib — no auth, no extra deps. Same parquet cache layout.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import warnings
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

from src.config import PROJECT_ROOT, get_settings

_CACHE_DIR = PROJECT_ROOT / "data" / "cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(symbol: str, timeframe: str) -> Path:
    return _CACHE_DIR / f"{symbol.upper()}_{timeframe}.parquet"


def load_daily_bars(
    symbols: Iterable[str],
    start: date,
    end: date,
    *,
    use_cache: bool = True,
    fallback_to_yfinance: bool = True,
) -> dict[str, pd.DataFrame]:
    """Daily OHLCV per symbol between `start` and `end`. Tries Alpaca, then yfinance."""
    out: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        path = _cache_path(sym, "1d")
        if use_cache and path.exists():
            df = pd.read_parquet(path)
            if _covers_requested_range(df, start, end):
                df = df.loc[(df.index.date >= start) & (df.index.date <= end)]
                if not df.empty:
                    out[sym] = df
                    continue

        df = _fetch_alpaca(sym, start, end)
        if (df is None or df.empty) and fallback_to_yfinance:
            warnings.warn(
                f"Falling back to yfinance for {sym}. Not for production sizing.",
                stacklevel=2,
            )
            df = _fetch_yfinance(sym, start, end)
        if df is not None and not df.empty:
            df.to_parquet(path)
            out[sym] = df
    return out


def _covers_requested_range(df: pd.DataFrame, start: date, end: date) -> bool:
    if df.empty:
        return False
    idx_dates = pd.to_datetime(df.index).date
    return min(idx_dates) <= start and max(idx_dates) >= end


def _fetch_alpaca(symbol: str, start: date, end: date) -> pd.DataFrame | None:
    s = get_settings()
    if not (s.ALPACA_API_KEY and s.ALPACA_SECRET_KEY):
        return None
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
    except ImportError:
        return None

    client = StockHistoricalDataClient(s.ALPACA_API_KEY, s.ALPACA_SECRET_KEY)
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=datetime.combine(start, datetime.min.time()),
        end=datetime.combine(end, datetime.min.time()),
    )
    bars = client.get_stock_bars(req)
    df = bars.df
    if df is None or df.empty:
        return None
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(symbol, level=0)
    df.columns = [c.lower() for c in df.columns]
    return df[["open", "high", "low", "close", "volume"]]


def _fetch_yfinance(symbol: str, start: date, end: date) -> pd.DataFrame | None:
    import yfinance as yf

    # auto_adjust=True applies the split/dividend adjustment factor backward through history,
    # so a historical close pre-split is divided by the split ratio. Without this, a 10:1
    # split prints as a ~-90% gap that triggers ATR-based stops on every affected day.
    df = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=True)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [str(c).lower() for c in df.columns]
    df = df[["open", "high", "low", "close", "volume"]]
    df.index = pd.to_datetime(df.index, utc=True)
    return df


# ---------------------------------------------------------------------------
# Crypto fetchers (public REST, no auth required)
# ---------------------------------------------------------------------------

_HTTP_TIMEOUT = 10  # seconds

_BINANCE_INTERVAL_MAP = {
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h", "8h": "8h", "12h": "12h",
    "1d": "1d", "3d": "3d", "1w": "1w", "1mo": "1M",
}

_COINBASE_GRANULARITY_MAP = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "6h": 21600,
    "1d": 86400,
}


def _normalize_crypto_symbol(symbol: str, source: str) -> str:
    """'BTC-USD' / 'BTC/USD' / 'BTCUSDT' -> source-specific canonical form."""
    s = symbol.upper().replace("/", "")
    if source == "binance":
        return s.replace("-", "")
    if source == "coinbase":
        if "-" in s:
            return s
        # Convert e.g. BTCUSD -> BTC-USD by splitting on a known quote currency.
        for quote in ("USDT", "USDC", "USD", "EUR", "BTC", "ETH"):
            if s.endswith(quote) and len(s) > len(quote):
                return f"{s[: -len(quote)]}-{quote}"
        return s
    return s


def _to_ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000)


def _http_get_json(url: str) -> object | None:
    """GET ``url`` and return parsed JSON, or None on any error (warning emitted)."""
    try:
        with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        warnings.warn(f"http fetch failed: {url!r}: {e!r}", stacklevel=3)
        return None
    except (json.JSONDecodeError, ValueError) as e:
        warnings.warn(f"http parse failed: {url!r}: {e!r}", stacklevel=3)
        return None


def _fetch_binance(
    symbol: str, start: date, end: date, interval: str = "1h"
) -> pd.DataFrame | None:
    """Pull spot klines from Binance public REST.

    Endpoint: GET https://api.binance.com/api/v3/klines

    Returns OHLCV DataFrame indexed by UTC timestamp; None if no rows or HTTP fails.
    """
    bin_interval = _BINANCE_INTERVAL_MAP.get(interval)
    if bin_interval is None:
        warnings.warn(f"unsupported binance interval {interval!r}", stacklevel=2)
        return None

    sym = _normalize_crypto_symbol(symbol, "binance")
    params = {
        "symbol": sym,
        "interval": bin_interval,
        "startTime": _to_ms(start),
        "endTime": _to_ms(end),
        "limit": 1000,
    }
    url = f"https://api.binance.com/api/v3/klines?{urllib.parse.urlencode(params)}"
    payload = _http_get_json(url)
    if not isinstance(payload, list) or not payload:
        return None

    rows = []
    for k in payload:
        # Binance kline: [openTime, o, h, l, c, v, closeTime, quoteAssetVolume, ...]
        try:
            rows.append(
                (
                    pd.to_datetime(int(k[0]), unit="ms", utc=True),
                    float(k[1]),
                    float(k[2]),
                    float(k[3]),
                    float(k[4]),
                    float(k[5]),
                )
            )
        except (TypeError, ValueError, IndexError):
            continue
    if not rows:
        return None

    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df = df.set_index("ts").sort_index()
    return df[["open", "high", "low", "close", "volume"]]


def _fetch_coinbase(
    symbol: str, start: date, end: date, interval: str = "1h"
) -> pd.DataFrame | None:
    """Pull from Coinbase Exchange public API.

    Endpoint: GET https://api.exchange.coinbase.com/products/{product}/candles

    The endpoint caps responses at 300 candles per request; for larger ranges this
    function paginates by stepping ``end`` backward until ``start`` is covered.
    """
    granularity = _COINBASE_GRANULARITY_MAP.get(interval)
    if granularity is None:
        warnings.warn(f"unsupported coinbase interval {interval!r}", stacklevel=2)
        return None

    product = _normalize_crypto_symbol(symbol, "coinbase")
    start_dt = datetime(start.year, start.month, start.day, tzinfo=UTC)
    end_dt = datetime(end.year, end.month, end.day, tzinfo=UTC)

    all_rows: list[tuple] = []
    cursor_end = end_dt
    max_pages = 50  # safety cap; 50 * 300 candles == well past typical use
    for _ in range(max_pages):
        if cursor_end <= start_dt:
            break
        params = {
            "granularity": granularity,
            "start": cursor_end.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "end": cursor_end.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        }
        # Coinbase returns 300 most recent candles ending at `end`.
        params["start"] = (
            max(start_dt, cursor_end - granularity * 300 * pd.Timedelta(seconds=1))
            .isoformat()
            .replace("+00:00", "Z")
        )
        url = (
            f"https://api.exchange.coinbase.com/products/{product}/candles"
            f"?{urllib.parse.urlencode(params)}"
        )
        payload = _http_get_json(url)
        if not isinstance(payload, list) or not payload:
            break

        # Coinbase candle: [time, low, high, open, close, volume]
        for c in payload:
            try:
                ts = pd.to_datetime(int(c[0]), unit="s", utc=True)
                all_rows.append(
                    (ts, float(c[3]), float(c[2]), float(c[1]), float(c[4]), float(c[5]))
                )
            except (TypeError, ValueError, IndexError):
                continue

        oldest_ts = pd.to_datetime(int(payload[-1][0]), unit="s", utc=True).to_pydatetime()
        if oldest_ts <= start_dt:
            break
        cursor_end = oldest_ts

    if not all_rows:
        return None
    df = pd.DataFrame(
        all_rows, columns=["ts", "open", "high", "low", "close", "volume"]
    )
    df = df.drop_duplicates(subset=["ts"]).set_index("ts").sort_index()
    df = df.loc[(df.index >= pd.Timestamp(start_dt)) & (df.index <= pd.Timestamp(end_dt))]
    if df.empty:
        return None
    return df[["open", "high", "low", "close", "volume"]]


def load_crypto_bars(
    symbols: Iterable[str],
    start: date,
    end: date,
    interval: str = "1h",
    *,
    source: str = "binance",
    use_cache: bool = True,
) -> dict[str, pd.DataFrame]:
    """Crypto OHLCV per symbol, mirroring `load_daily_bars` but for 24/7 markets.

    Parameters
    ----------
    symbols : iterable of str
        e.g. ('BTCUSDT', 'ETHUSDT'). Source-appropriate normalization is applied.
    start, end : date
        UTC date range.
    interval : str
        '1h' (default), '5m', '15m', '1d', etc. Source-specific support varies.
    source : {'binance', 'coinbase'}
        Which public REST endpoint to use.
    use_cache : bool
        Read/write parquet cache at ``data/cache/<SYMBOL>_<interval>.parquet``.
    """
    src = source.lower().strip()
    if src not in ("binance", "coinbase"):
        raise ValueError(f"unsupported crypto source {source!r}")

    fetch = _fetch_binance if src == "binance" else _fetch_coinbase
    out: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        path = _cache_path(sym, interval)
        if use_cache and path.exists():
            try:
                df = pd.read_parquet(path)
                if _covers_requested_range_ts(df, start, end):
                    df = df.loc[
                        (df.index >= pd.Timestamp(start, tz="UTC"))
                        & (df.index <= pd.Timestamp(end, tz="UTC"))
                    ]
                    if not df.empty:
                        out[sym] = df
                        continue
            except (OSError, ValueError):
                pass  # fall through to fetch

        df = fetch(sym, start, end, interval)
        if df is not None and not df.empty:
            try:
                df.to_parquet(path)
            except OSError as e:  # pragma: no cover - filesystem edge case
                warnings.warn(f"failed to cache {sym}: {e!r}", stacklevel=2)
            out[sym] = df
    return out


def _covers_requested_range_ts(df: pd.DataFrame, start: date, end: date) -> bool:
    """Same idea as `_covers_requested_range` but for tz-aware timestamp indexes."""
    if df.empty:
        return False
    idx = pd.to_datetime(df.index, utc=True)
    return idx.min().date() <= start and idx.max().date() >= end
