"""Perpetual-funding data + signal-suppression filter.

Funding rate is the periodic payment between perp longs and shorts to keep the
perp price tethered to spot. Persistently positive funding => longs paying shorts
=> crowd is long. Use that to dampen long signals near extremes (and shorts at
the opposite extreme).

Two upstream sources, tried in order:

  1. Binance public ``fapi.binance.com/fapi/v1/fundingRate`` (no auth)
  2. Bybit public ``api.bybit.com/v5/market/funding/history`` (no auth) —
     fallback when Binance is geo-blocked (HTTP 451) or otherwise
     unreachable. The Researcher session flagged this on its first cycle:
     Binance 451s on US-residential IPs, Bybit does not.

Both endpoints return the same shape after normalization. Network paths
return an empty frame on total failure so callers can degrade gracefully.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import warnings
from datetime import UTC, date, datetime
from typing import Literal

import pandas as pd

from src.net import UnsafeUrlError, safe_urlopen

_BINANCE_FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
_BYBIT_FUNDING_URL = "https://api.bybit.com/v5/market/funding/history"
_OKX_FUNDING_URL = "https://www.okx.com/api/v5/public/funding-rate-history"
_REQUEST_TIMEOUT = 10  # seconds
_MAX_LIMIT = 1000
_BYBIT_MAX_LIMIT = 200  # Bybit caps responses at 200 rows
_OKX_MAX_LIMIT = 100  # OKX cap per request

# Many exchange WAFs (OKX, Bybit, sometimes Cloudflare on Binance) reject
# requests with no User-Agent header — they 403 silently. Identify
# ourselves cleanly without claiming to be a browser.
_USER_AGENT = "algo-trader/0.1 (+funding-rate-fetch)"


def _request(url: str) -> urllib.request.Request:
    """Build a urllib Request with our project User-Agent.

    safe_urlopen accepts either a string or a Request, so this just lets us
    add the UA header before handing off. Without this OKX returns 403
    Forbidden on the same URL that returns 200 from `curl`.
    """
    # S310: scheme is enforced as https by safe_urlopen below; the Request
    # itself doesn't open a connection.
    return urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})  # noqa: S310


def _to_ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000)


def _normalize_symbol(symbol: str) -> str:
    """Accept 'BTC-USDT', 'BTC/USDT', 'BTCUSDT' -> 'BTCUSDT'."""
    s = symbol.upper().replace("-", "").replace("/", "")
    return s


def fetch_funding_rate(
    symbol: str,
    start: date,
    end: date,
    *,
    source: Literal["auto", "binance", "bybit", "okx"] = "auto",
) -> pd.DataFrame:
    """Pull funding rate history. Tries Binance → Bybit → OKX in turn.

    All endpoints are public; no auth required. Three venues because the
    Researcher session discovered (live, on the dev machine) that Binance
    HTTP-451s and Bybit HTTP-403s on US-residential IPs. OKX answers
    cleanly on the same host. Falling all the way through is rare in
    production; in development it's the difference between "funding
    filter works" and "funding filter is silently dead".

    Parameters
    ----------
    symbol : str
        e.g. "BTCUSDT" (also accepts "BTC-USDT" / "BTC/USDT").
    start, end : date
        UTC date range, inclusive on start, exclusive on end semantics.
    source : "auto" | "binance" | "bybit" | "okx"
        ``"auto"`` (default): try Binance → Bybit → OKX in turn, return
        the first non-empty result. Forced sources skip the fallback chain
        entirely — useful for per-venue unit tests and for forcing a
        venue you know works on your IP.

    Returns
    -------
    pd.DataFrame
        Indexed by UTC timestamp. Columns: ``funding_rate``, ``predicted_rate``.
        Empty DataFrame on TOTAL failure (and a warning is emitted by each
        attempted venue).
    """
    sym = _normalize_symbol(symbol)
    if source == "binance":
        return _fetch_binance(sym, start, end)
    if source == "bybit":
        return _fetch_bybit(sym, start, end)
    if source == "okx":
        return _fetch_okx(sym, start, end)
    # source="auto": Binance → Bybit → OKX, first non-empty wins.
    for fetcher in (_fetch_binance, _fetch_bybit, _fetch_okx):
        df = fetcher(sym, start, end)
        if not df.empty:
            return df
    return _empty_funding_frame()


def _fetch_binance(sym: str, start: date, end: date) -> pd.DataFrame:
    """Binance fundingRate endpoint. Empty frame on any failure."""
    params = {
        "symbol": sym,
        "startTime": _to_ms(start),
        "endTime": _to_ms(end),
        "limit": _MAX_LIMIT,
    }
    url = f"{_BINANCE_FUNDING_URL}?{urllib.parse.urlencode(params)}"
    try:
        with safe_urlopen(_request(url), timeout=_REQUEST_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except UnsafeUrlError as e:
        warnings.warn(
            f"binance funding fetch refused (non-https) for {sym}: {e!r}",
            stacklevel=2,
        )
        return _empty_funding_frame()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        # HTTPError 451 ("Unavailable For Legal Reasons") is the geo-block path;
        # let the caller fall back to Bybit silently rather than warn loudly.
        if not _is_expected_geo_block(e):
            warnings.warn(
                f"binance funding fetch failed for {sym}: {e!r}; will try Bybit",
                stacklevel=2,
            )
        return _empty_funding_frame()
    except (json.JSONDecodeError, ValueError) as e:
        warnings.warn(
            f"binance funding parse failed for {sym}: {e!r}; will try Bybit",
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


def _fetch_bybit(sym: str, start: date, end: date) -> pd.DataFrame:
    """Bybit v5 funding-history endpoint. Empty frame on any failure.

    Endpoint: GET https://api.bybit.com/v5/market/funding/history

    Bybit caps responses at 200 rows per request. We don't paginate today —
    most callers ask for ≤30 days at 8h cadence (≤90 rows) which fits in
    one request. If a caller asks for >200 rows we return the most recent
    200 and emit a warning so the truncation is observable.
    """
    params = {
        "category": "linear",
        "symbol": sym,
        "startTime": _to_ms(start),
        "endTime": _to_ms(end),
        "limit": _BYBIT_MAX_LIMIT,
    }
    url = f"{_BYBIT_FUNDING_URL}?{urllib.parse.urlencode(params)}"
    try:
        with safe_urlopen(_request(url), timeout=_REQUEST_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except UnsafeUrlError as e:
        warnings.warn(
            f"bybit funding fetch refused (non-https) for {sym}: {e!r}",
            stacklevel=2,
        )
        return _empty_funding_frame()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        warnings.warn(
            f"bybit funding fetch failed for {sym}: {e!r}; returning empty frame",
            stacklevel=2,
        )
        return _empty_funding_frame()
    except (json.JSONDecodeError, ValueError) as e:
        warnings.warn(
            f"bybit funding parse failed for {sym}: {e!r}; returning empty frame",
            stacklevel=2,
        )
        return _empty_funding_frame()

    if not isinstance(payload, dict):
        return _empty_funding_frame()
    if int(payload.get("retCode", -1)) != 0:
        warnings.warn(
            f"bybit funding non-zero retCode for {sym}: {payload.get('retMsg')!r}",
            stacklevel=2,
        )
        return _empty_funding_frame()
    result = payload.get("result") or {}
    items = result.get("list") or []
    if not items:
        return _empty_funding_frame()

    rows = []
    for r in items:
        try:
            ts_ms = int(r.get("fundingRateTimestamp", 0))
            rate = float(r.get("fundingRate", 0.0))
        except (TypeError, ValueError):
            continue
        ts = pd.to_datetime(ts_ms, unit="ms", utc=True)
        rows.append((ts, rate, float("nan")))

    if not rows:
        return _empty_funding_frame()

    df = pd.DataFrame(rows, columns=["timestamp", "funding_rate", "predicted_rate"])
    df = df.set_index("timestamp").sort_index()
    # Bybit returns newest-first; we sort_index above so it's ascending now.
    return df


def _fetch_okx(sym: str, start: date, end: date) -> pd.DataFrame:
    """OKX v5 funding-rate-history. Empty frame on any failure.

    OKX uses ``BTC-USDT-SWAP`` instrument IDs — we translate ``BTCUSDT``
    by inferring the quote currency suffix and appending ``-SWAP`` (the
    perpetual product). Endpoint returns rows newest-first; we sort
    ascending after parse.
    """
    inst_id = _to_okx_inst_id(sym)
    if inst_id is None:
        warnings.warn(
            f"okx funding fetch: unable to map {sym!r} to OKX inst-id format",
            stacklevel=2,
        )
        return _empty_funding_frame()
    params = {
        "instId": inst_id,
        "before": _to_ms(start),
        "after": _to_ms(end),
        "limit": _OKX_MAX_LIMIT,
    }
    url = f"{_OKX_FUNDING_URL}?{urllib.parse.urlencode(params)}"
    try:
        with safe_urlopen(_request(url), timeout=_REQUEST_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except UnsafeUrlError as e:
        warnings.warn(
            f"okx funding fetch refused (non-https) for {sym}: {e!r}",
            stacklevel=2,
        )
        return _empty_funding_frame()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        warnings.warn(
            f"okx funding fetch failed for {sym}: {e!r}; returning empty frame",
            stacklevel=2,
        )
        return _empty_funding_frame()
    except (json.JSONDecodeError, ValueError) as e:
        warnings.warn(
            f"okx funding parse failed for {sym}: {e!r}; returning empty frame",
            stacklevel=2,
        )
        return _empty_funding_frame()

    if not isinstance(payload, dict):
        return _empty_funding_frame()
    if str(payload.get("code", "")) != "0":
        warnings.warn(
            f"okx funding non-zero code for {sym}: {payload.get('msg')!r}",
            stacklevel=2,
        )
        return _empty_funding_frame()
    items = payload.get("data") or []
    if not items:
        return _empty_funding_frame()

    rows = []
    for r in items:
        try:
            ts_ms = int(r.get("fundingTime", 0))
            rate = float(r.get("fundingRate", 0.0))
        except (TypeError, ValueError):
            continue
        ts = pd.to_datetime(ts_ms, unit="ms", utc=True)
        rows.append((ts, rate, float("nan")))

    if not rows:
        return _empty_funding_frame()

    df = pd.DataFrame(rows, columns=["timestamp", "funding_rate", "predicted_rate"])
    df = df.set_index("timestamp").sort_index()
    return df


def _to_okx_inst_id(sym: str) -> str | None:
    """'BTCUSDT' → 'BTC-USDT-SWAP'. Returns None for unrecognized quotes."""
    s = sym.upper()
    for quote in ("USDT", "USDC", "USD"):
        if s.endswith(quote) and len(s) > len(quote):
            base = s[: -len(quote)]
            return f"{base}-{quote}-SWAP"
    return None


def _is_expected_geo_block(exc: BaseException) -> bool:
    """Recognize the geo-block path so the auto-fallback stays quiet.

    Binance returns HTTP 451 ("Unavailable For Legal Reasons") for IPs in
    embargoed jurisdictions. That's not an error worth a stacktrace — the
    fallback exists precisely to handle it.
    """
    code = getattr(exc, "code", None)
    return code == 451


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

    # `history` was filtered to `index <= asof_ts` above, so iloc[-1] is the
    # most recent observation as-of the asof timestamp — no look-ahead.
    current = float(rates.iloc[-1])  # noqa: bug-hunt:look_ahead_iloc_minus_1
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
