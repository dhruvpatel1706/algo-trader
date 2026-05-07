"""Analyst agent — multi-step pre-trade review.

Replaces the shallow ``autonomous_reasoner`` "multiplier 0..1" judgment with
a structured, auditable verdict that incorporates:

  1. **TradingView multi-timeframe ratings** (1D, 4H, 1H) — independent
     signal we don't generate, sourced from the public ``tradingview-ta``
     library. Catches "you're trying to long into a STRONG_SELL daily"
     setups the rule-based strategy misses.
  2. **Chart context** derived from the bars dict (recent swing levels,
     range, volatility, last-bar quality).
  3. **R:R / probability calculation** from the strategy's stop+target
     plus a simple win-probability proxy from TV vote ratios.
  4. **LLM synthesis** (when available) — produces the human-readable
     reasoning. Falls back to a deterministic rule-based composer when
     the LLM router is dead so the bot keeps a healthy decision policy.

The output is :class:`AnalystVerdict` — a structured, journalable record
the operator can audit. Every field has a comment so the dashboard can
show "why did we say no to ETH?" without anyone reading source.

Design principles:
  - **Hard veto** on a small set of known-bad combos (e.g. long signal
    when daily TV reads STRONG_SELL). Vetos are deterministic and
    survive LLM outages.
  - **Soft dampening** for borderline setups (multiplier in [0, 1]
    similar to the old reasoner) — used by the trade pipeline for
    sizing.
  - **No I/O during construction**; all I/O is in :meth:`evaluate` so
    tests can construct the analyst without touching the network.
  - **Graceful degradation**: TV fetch failures, LLM failures, and
    missing bars all degrade to safe defaults rather than aborting.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from threading import Lock
from typing import Any, Literal

log = logging.getLogger(__name__)


# In-process TTL cache for TV ratings. The TV scanner is rate-limited
# (~free-tier 429 after a burst of ~30 calls) and ratings on 1D/4H
# barely change minute-to-minute, so caching for a few minutes is a big
# win. Keyed by (symbol, screener, primary-exchange, timeframe). Stores
# (ts, TimeframeRating | None | _MISS_SENTINEL).
_RATING_CACHE: dict[tuple[str, str, str, str], tuple[float, Any]] = {}
_RATING_CACHE_LOCK = Lock()
_RATING_CACHE_TTL_SECONDS: float = 300.0  # 5 minutes — daily ratings barely move
_MISS_SENTINEL = object()


# Mapping from TradingView's verbose ratings to a normalised score in
# [-2, +2]. Used to combine ratings across timeframes.
_RATING_SCORE: dict[str, int] = {
    "STRONG_SELL": -2,
    "SELL": -1,
    "NEUTRAL": 0,
    "BUY": 1,
    "STRONG_BUY": 2,
}


# Hard-veto combinations. (signal_side, mtf_label, threshold) — if ANY
# timeframe at or below the threshold for the listed label, the analyst
# rejects the signal outright. Tightest gate is on the daily; lower
# timeframes can disagree with the trade idea more often without veto.
_VETO_RULES: tuple[tuple[str, str, int], ...] = (
    ("buy", "1D", -2),  # never long into STRONG_SELL daily
    ("buy", "4H", -2),  # never long into STRONG_SELL 4H
)


# Default screener / exchange routing. Crypto strategies get the
# Binance crypto screener; equity strategies get america/NASDAQ. Override
# per-strategy via ``Analyst(screener_overrides={"DDOG": ("america", "NASDAQ")})``
# if a symbol's home exchange differs.
_DEFAULT_SCREENER_BY_ASSET_CLASS: dict[str, tuple[str, str]] = {
    "crypto": ("crypto", "BINANCE"),
    "equity": ("america", "NASDAQ"),
    # gold / bonds / silver all run on NYSE-listed ETFs in our universe.
    "gold": ("america", "AMEX"),
    "silver": ("america", "AMEX"),
    "bonds": ("america", "NASDAQ"),
}


@dataclass(slots=True)
class TimeframeRating:
    """One row of TradingView's analyst summary at one timeframe."""

    timeframe: str  # "1D" | "4H" | "1H"
    recommendation: str  # STRONG_BUY/BUY/NEUTRAL/SELL/STRONG_SELL
    buy: int
    sell: int
    neutral: int
    score: int  # _RATING_SCORE[recommendation]

    @property
    def total(self) -> int:
        return self.buy + self.sell + self.neutral

    @property
    def buy_ratio(self) -> float:
        return self.buy / self.total if self.total else 0.0


@dataclass(slots=True)
class AnalystVerdict:
    """Structured pre-trade review record. Every field is journal-safe.

    The trade pipeline reads ``accept`` (hard gate) and ``multiplier``
    (sizing knob). The remaining fields are for the operator + dashboard
    to audit decisions.
    """

    # Hard accept/reject. ``accept=False`` means refuse the trade —
    # equivalent to the old ``halt=True`` from the reasoner.
    accept: bool

    # Confidence multiplier for sizing. 1.0 = full size, 0.5 = half size,
    # 0.0 = effective veto (also implies accept=False). The trade
    # pipeline still applies its standard cumulative-cap math on top.
    multiplier: float

    # Estimated probability the trade hits target before stop. Computed
    # from a weighted blend of TV bullish-vote ratio and the strategy's
    # rule confidence; clipped to [0.05, 0.95]. Use cautiously — this is
    # a rough proxy, not a calibrated probability.
    win_probability: float

    # Expected R-multiple under the analyst's win_probability. >0 = +EV.
    # Computed from (target_R * p_win) - (1 * p_loss). Strategies emit
    # signals with a fixed target_R; this just attaches a probability
    # estimate.
    expected_r: float

    # Human-readable trace. Either generated by the LLM or composed
    # deterministically from the rule-based path. Always populated so
    # the operator never has to dig in code to understand a verdict.
    reasoning: str

    # If accept=False, the specific veto reason. None when accept=True.
    veto_reason: str | None

    # Per-timeframe ratings (already serialisable).
    timeframe_ratings: list[TimeframeRating] = field(default_factory=list)

    # Free-form catalyst note populated when a relevant news/earnings
    # event was detected. None when no catalyst was checked or found.
    catalyst: str | None = None

    # Source label so the journal shows which path produced the verdict.
    source: Literal["llm", "rules", "rules-llm-failed"] = "rules"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["timeframe_ratings"] = [asdict(r) for r in self.timeframe_ratings]
        return d


# ---------------------------------------------------------------------------
# Analyst implementation
# ---------------------------------------------------------------------------


class Analyst:
    """Multi-step pre-trade analyst.

    Wraps three independent signal sources — TradingView multi-timeframe
    ratings, chart context derived from the bars dict, and (optionally)
    an LLM router — into a single structured verdict the trade pipeline
    can act on.
    """

    def __init__(
        self,
        *,
        llm_router: Any | None = None,
        rating_fetcher: Any | None = None,
        timeframes: tuple[str, ...] = ("1D", "4H", "1H"),
        screener_overrides: dict[str, tuple[str, str]] | None = None,
    ) -> None:
        """Construct the analyst.

        Parameters
        ----------
        llm_router
            Optional LLM router (e.g. ``src.llm.router.Router``). If
            provided, the analyst calls it for the final synthesis step
            and uses the structured verdict from the model. If absent or
            failing, the analyst falls back to deterministic rule-based
            composition.
        rating_fetcher
            Callable ``fetch(symbol, screener, exchange, interval) ->
            TimeframeRating`` used to fetch TV ratings. Defaults to a
            ``tradingview-ta`` adapter; tests inject a stub. The analyst
            never crashes on fetcher errors — it logs and continues with
            whatever ratings did succeed.
        timeframes
            Which timeframes to fetch. Default 1D + 4H + 1H gives weekly
            trend, daily trend, intra-day trigger.
        screener_overrides
            Per-symbol ``{symbol: (screener, exchange)}`` map for cases
            where the default asset-class routing isn't right (e.g. a
            crypto pair only on KuCoin).
        """
        self._llm = llm_router
        self._fetch_rating = rating_fetcher or _default_rating_fetcher
        self._timeframes = timeframes
        self._overrides = dict(screener_overrides or {})

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(self, signal: Any, ctx: Any) -> AnalystVerdict:
        """Produce a verdict for one signal.

        ``signal`` must expose ``.symbol`` (str), ``.side`` ('buy' or
        'sell'), ``.entry`` (Decimal-like), ``.stop`` (Decimal-like),
        ``.target`` (Decimal-like or None), ``.confidence`` (float),
        ``.strategy_tag`` (str), ``.asset_class`` (optional, str).

        ``ctx`` is the existing :class:`SignalContext` shape. Only
        ``ctx.recent_bars`` is used here; pass ``None`` and the chart
        context section degrades gracefully.
        """
        screener, exchange = self._route(signal)
        ratings = self._gather_ratings(signal.symbol, screener, exchange)
        chart = self._summarize_chart(getattr(ctx, "recent_bars", None))
        veto = self._check_veto(signal, ratings)
        if veto is not None:
            return _veto_verdict(signal, ratings, veto)

        rule_verdict = self._compose_rule_verdict(signal, ratings, chart)

        # If LLM is configured, ask for a deeper synthesis. Always pass
        # the rule_verdict as a baseline — the LLM can disagree but we
        # keep its output structured.
        if self._llm is not None:
            llm_verdict = self._compose_llm_verdict(signal, ratings, chart, rule_verdict)
            if llm_verdict is not None:
                return llm_verdict
            # LLM failed — keep the rule verdict but tag the source.
            rule_verdict.source = "rules-llm-failed"

        return rule_verdict

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def _route(self, signal: Any) -> tuple[str, str]:
        sym = signal.symbol
        if sym in self._overrides:
            return self._overrides[sym]
        asset_class = (getattr(signal, "asset_class", None) or "").lower()
        if asset_class and asset_class in _DEFAULT_SCREENER_BY_ASSET_CLASS:
            return _DEFAULT_SCREENER_BY_ASSET_CLASS[asset_class]
        # Crypto fallback: USDT/USD suffix is a strong heuristic.
        if sym.endswith("USDT") or sym.endswith("USD"):
            return _DEFAULT_SCREENER_BY_ASSET_CLASS["crypto"]
        return _DEFAULT_SCREENER_BY_ASSET_CLASS["equity"]

    # ------------------------------------------------------------------
    # Multi-timeframe rating gather
    # ------------------------------------------------------------------

    def _gather_ratings(
        self, symbol: str, screener: str, exchange: str
    ) -> list[TimeframeRating]:
        out: list[TimeframeRating] = []
        for tf in self._timeframes:
            try:
                r = self._fetch_rating(symbol, screener, exchange, tf)
            except Exception as e:
                log.warning(
                    "Analyst: TV rating fetch failed for %s @ %s: %s",
                    symbol, tf, e,
                )
                continue
            if r is not None:
                out.append(r)
        return out

    # ------------------------------------------------------------------
    # Chart context derivation
    # ------------------------------------------------------------------

    @staticmethod
    def _summarize_chart(bars: Any) -> dict[str, Any]:
        """Extract a compact chart summary the LLM can read.

        We don't use the bars in the rule-based composer (TV ratings are
        the substantive signal there); this is for the LLM prompt only.
        Returns a dict with last_close, last_range_pct, n_bars,
        recent_swing_high, recent_swing_low. Empty dict when bars is
        None or empty.
        """
        if bars is None:
            return {}
        try:
            recent = list(bars)[-20:]
            if not recent:
                return {}
            highs = [float(b.get("high", 0)) for b in recent if isinstance(b, dict)]
            lows = [float(b.get("low", 0)) for b in recent if isinstance(b, dict)]
            closes = [float(b.get("close", 0)) for b in recent if isinstance(b, dict)]
            if not closes or not highs or not lows:
                return {}
            return {
                "last_close": closes[-1],
                "n_bars": len(recent),
                "recent_swing_high": max(highs),
                "recent_swing_low": min(lows),
                "range_pct": (max(highs) - min(lows)) / closes[-1] if closes[-1] else 0.0,
            }
        except (KeyError, TypeError, ValueError):
            return {}

    # ------------------------------------------------------------------
    # Veto check
    # ------------------------------------------------------------------

    @staticmethod
    def _check_veto(
        signal: Any, ratings: list[TimeframeRating]
    ) -> str | None:
        """Apply the deterministic hard-veto rules. Returns a reason
        string when vetoed, None otherwise."""
        side = (getattr(signal, "side", "") or "").lower()
        by_tf = {r.timeframe: r for r in ratings}
        for vside, tf, threshold in _VETO_RULES:
            if vside != side:
                continue
            r = by_tf.get(tf)
            if r is None:
                continue
            if r.score <= threshold:
                return (
                    f"VETO: {side} signal on {signal.symbol} but TV {tf} "
                    f"reads {r.recommendation} ({r.buy} buy / {r.sell} sell / "
                    f"{r.neutral} neutral) — would be fading a confirmed trend."
                )
        return None

    # ------------------------------------------------------------------
    # Rule-based composition
    # ------------------------------------------------------------------

    @staticmethod
    def _compose_rule_verdict(
        signal: Any,
        ratings: list[TimeframeRating],
        chart: dict[str, Any],
    ) -> AnalystVerdict:
        """Compose a verdict from TV ratings + signal alone, no LLM.

        The multiplier scales 0.5..1.2 based on multi-timeframe alignment:
          - All TFs agree with signal direction (STRONG_BUY / BUY for a
            long): boost to 1.2 (cap)
          - Top TF (daily) agrees with signal: 1.0
          - Top TF neutral: 0.8 (proceed but smaller)
          - Top TF disagrees but no veto fired: 0.5 (still smaller)
        """
        side = (getattr(signal, "side", "") or "").lower()
        signed = +1 if side == "buy" else -1
        if not ratings:
            # No TV data at all — be conservative but don't reject. Pass
            # the strategy's confidence through unchanged. Better than
            # blocking trades when TV is unreachable.
            return AnalystVerdict(
                accept=True,
                multiplier=1.0,
                win_probability=float(getattr(signal, "confidence", 0.5)),
                expected_r=0.0,
                reasoning=(
                    "TV ratings unavailable — proceeding on strategy "
                    "confidence alone."
                ),
                veto_reason=None,
                timeframe_ratings=[],
                catalyst=None,
                source="rules",
            )

        # Compute aligned score: positive = agrees with signal direction.
        weights = {"1D": 0.5, "4H": 0.3, "1H": 0.2}
        total_w = sum(weights.get(r.timeframe, 0.1) for r in ratings)
        aligned = sum(
            (r.score * signed) * weights.get(r.timeframe, 0.1)
            for r in ratings
        ) / max(total_w, 1e-9)
        # aligned now in roughly [-2, +2].

        if aligned >= 1.5:
            multiplier = 1.2  # boost for clear alignment
        elif aligned >= 0.5:
            multiplier = 1.0
        elif aligned >= -0.5:
            multiplier = 0.8
        else:
            multiplier = 0.5

        # win_probability: blend rule confidence with TV's bullish ratio
        # on the top timeframe in the signal direction.
        top = next((r for r in ratings if r.timeframe == "1D"), ratings[0])
        if signed > 0:
            tv_p = top.buy_ratio
        else:
            tv_p = top.sell / top.total if top.total else 0.0
        rule_p = float(getattr(signal, "confidence", 0.55))
        win_probability = max(0.05, min(0.95, 0.5 * rule_p + 0.5 * tv_p))

        # expected_r: the strategy attaches a target_R; if absent,
        # estimate from entry/target/stop.
        target_r = _estimate_target_r(signal)
        expected_r = (target_r * win_probability) - (1.0 * (1.0 - win_probability))

        # Reasoning string — short, dashboard-friendly.
        rating_str = ", ".join(
            f"{r.timeframe}={r.recommendation}({r.buy}/{r.sell}/{r.neutral})"
            for r in ratings
        )
        chart_str = ""
        if chart:
            chart_str = (
                f" Last close {chart.get('last_close', 0):.4f}; recent range "
                f"{(chart.get('range_pct', 0) * 100):.1f}%."
            )
        reasoning = (
            f"{signal.strategy_tag} {side} on {signal.symbol}. "
            f"TV: {rating_str}. Aligned score {aligned:+.2f} → multiplier "
            f"{multiplier:.2f}. p_win~{win_probability:.0%}, "
            f"expected_R≈{expected_r:+.2f}.{chart_str}"
        )

        return AnalystVerdict(
            accept=True,
            multiplier=multiplier,
            win_probability=win_probability,
            expected_r=expected_r,
            reasoning=reasoning,
            veto_reason=None,
            timeframe_ratings=ratings,
            catalyst=None,
            source="rules",
        )

    # ------------------------------------------------------------------
    # LLM-augmented composition
    # ------------------------------------------------------------------

    def _compose_llm_verdict(
        self,
        signal: Any,
        ratings: list[TimeframeRating],
        chart: dict[str, Any],
        baseline: AnalystVerdict,
    ) -> AnalystVerdict | None:
        """Ask the LLM for a structured verdict; return None on failure.

        The LLM receives:
          - The strategy signal as a JSON object
          - TV multi-timeframe ratings
          - Compact chart summary
          - The rule-based baseline verdict

        It must return JSON with keys: accept (bool), multiplier (float),
        win_probability (float), expected_r (float), reasoning (str),
        veto_reason (str | null), catalyst (str | null).
        """
        try:
            system, user = _build_llm_prompt(signal, ratings, chart, baseline)
            response = self._llm.call(system=system, user=user, max_tokens=500)
            parsed = _parse_llm_verdict(response.text)
        except Exception as e:
            log.warning("Analyst: LLM synthesis failed: %s", e)
            return None
        if parsed is None:
            return None
        # Guard rails: clamp multiplier and probability into safe ranges
        # so a hallucinated "1.5x" doesn't oversize.
        multiplier = max(0.0, min(1.2, float(parsed.get("multiplier", 1.0))))
        win_probability = max(0.05, min(0.95, float(parsed.get("win_probability", 0.5))))
        expected_r = float(parsed.get("expected_r", 0.0))
        accept = bool(parsed.get("accept", True)) and multiplier > 0.0
        return AnalystVerdict(
            accept=accept,
            multiplier=multiplier,
            win_probability=win_probability,
            expected_r=expected_r,
            reasoning=str(parsed.get("reasoning") or baseline.reasoning),
            veto_reason=parsed.get("veto_reason"),
            timeframe_ratings=ratings,
            catalyst=parsed.get("catalyst"),
            source="llm",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _veto_verdict(
    signal: Any,
    ratings: list[TimeframeRating],
    veto_reason: str,
) -> AnalystVerdict:
    return AnalystVerdict(
        accept=False,
        multiplier=0.0,
        win_probability=0.0,
        expected_r=-1.0,
        reasoning=veto_reason,
        veto_reason=veto_reason,
        timeframe_ratings=ratings,
        catalyst=None,
        source="rules",
    )


def _estimate_target_r(signal: Any) -> float:
    """Pull the strategy's R-multiple target from entry/stop/target.

    R = (target - entry) / (entry - stop). Defaults to 2.0 when target
    is None or any value is non-positive (we don't want to inflate EV
    on missing data).
    """
    try:
        entry = float(signal.entry)
        stop = float(signal.stop)
        target = signal.target
        if target is None:
            return 2.0
        target = float(target)
        risk = entry - stop
        reward = target - entry
        if risk <= 0 or reward <= 0:
            return 2.0
        return reward / risk
    except (AttributeError, TypeError, ValueError):
        return 2.0


# ---------------------------------------------------------------------------
# Default rating fetcher — uses tradingview-ta library
# ---------------------------------------------------------------------------

_TF_TO_TV_INTERVAL: dict[str, str] = {
    "1D": "1d",
    "4H": "4h",
    "1H": "1h",
    "15m": "15m",
    "5m": "5m",
}


# For crypto pairs, the TV scanner sometimes returns "Exchange or symbol
# not found" on Binance even for liquid pairs. Falling through a small
# chain of major exchanges recovers most of those cases. Equity uses
# the requested exchange first, then NYSE as a backup since some tickers
# we configure as NASDAQ may actually trade on NYSE.
_CRYPTO_EXCHANGE_CHAIN: tuple[str, ...] = ("BINANCE", "KUCOIN", "BYBIT", "MEXC")
_EQUITY_EXCHANGE_CHAIN: tuple[str, ...] = ("NASDAQ", "NYSE", "AMEX")


def _exchange_chain(screener: str, primary: str) -> tuple[str, ...]:
    """Return an ordered list of exchanges to try for one screener.

    Always starts with the caller's primary exchange so existing routing
    decisions still take precedence. Then falls through the rest of the
    venue list, dedup'd. This is a 'try the next venue when the symbol
    isn't found' fallback — it does NOT change the routing semantics
    (crypto symbols still go to crypto screener; equity to america).
    """
    if screener == "crypto":
        chain = _CRYPTO_EXCHANGE_CHAIN
    elif screener == "america":
        chain = _EQUITY_EXCHANGE_CHAIN
    else:
        chain = (primary,)
    seen = []
    for ex in (primary, *chain):
        if ex and ex not in seen:
            seen.append(ex)
    return tuple(seen)


def _default_rating_fetcher(
    symbol: str, screener: str, exchange: str, timeframe: str
) -> TimeframeRating | None:
    """Fetch a single TV rating via tradingview-ta with exchange fallback.

    The TV public scanner is occasionally moody about which exchange
    hosts a pair (especially for crypto across BINANCE/KUCOIN/BYBIT).
    We try each exchange in turn until one returns analysis data;
    return None only when the whole chain fails.

    A short-lived in-memory cache (TTL 5 min) prevents repeated hits to
    the TV public scanner from blowing through its free-tier rate
    limit when multiple strategies or agents evaluate the same symbol
    in quick succession.
    """
    cache_key = (symbol, screener, exchange, timeframe)
    now = time.monotonic()
    with _RATING_CACHE_LOCK:
        cached = _RATING_CACHE.get(cache_key)
        if cached is not None:
            ts, value = cached
            if (now - ts) < _RATING_CACHE_TTL_SECONDS:
                if value is _MISS_SENTINEL:
                    return None
                return value
            # Stale — drop and refetch.
            _RATING_CACHE.pop(cache_key, None)

    try:
        # Lazy import: keep the rest of the module importable without
        # the optional dep installed (e.g. for tests that stub).
        from tradingview_ta import (  # noqa: PLC0415
            Interval,
            TA_Handler,
        )
    except ImportError:
        log.warning("tradingview-ta not installed; analyst falls back to no-TV")
        return None

    iv_lookup = {
        "1D": Interval.INTERVAL_1_DAY,
        "4H": Interval.INTERVAL_4_HOURS,
        "1H": Interval.INTERVAL_1_HOUR,
        "15m": Interval.INTERVAL_15_MINUTES,
        "5m": Interval.INTERVAL_5_MINUTES,
    }
    tv_interval = iv_lookup.get(timeframe)
    if tv_interval is None:
        return None

    last_err: Exception | None = None
    for venue in _exchange_chain(screener, exchange):
        try:
            handler = TA_Handler(
                symbol=symbol,
                screener=screener,
                exchange=venue,
                interval=tv_interval,
            )
            a = handler.get_analysis()
        except Exception as e:
            last_err = e
            # 429 is a global rate-limit, not a per-exchange problem —
            # falling through to the next venue makes it worse. Break
            # out, cache the miss for the TTL, and let the agent tick
            # naturally retry once the limit cools.
            msg = str(e)
            if "429" in msg or "rate limit" in msg.lower():
                break
            continue
        if a is None:
            continue
        summary = a.summary or {}
        reco = summary.get("RECOMMENDATION", "NEUTRAL")
        rating = TimeframeRating(
            timeframe=timeframe,
            recommendation=reco,
            buy=int(summary.get("BUY", 0)),
            sell=int(summary.get("SELL", 0)),
            neutral=int(summary.get("NEUTRAL", 0)),
            score=_RATING_SCORE.get(reco, 0),
        )
        with _RATING_CACHE_LOCK:
            _RATING_CACHE[cache_key] = (now, rating)
        return rating
    # Whole chain failed. Cache the miss briefly so we don't keep
    # hammering TV during an outage; the TTL is short enough that a
    # transient 429 self-heals on the next agent tick.
    with _RATING_CACHE_LOCK:
        _RATING_CACHE[cache_key] = (now, _MISS_SENTINEL)
    if last_err is not None:
        # Reraise so the caller's per-timeframe try/except logs it.
        raise last_err
    return None


# ---------------------------------------------------------------------------
# LLM prompt construction + response parsing
# ---------------------------------------------------------------------------


_LLM_SYSTEM_PROMPT = """You are a professional trading analyst reviewing a single
trade idea before it goes to a paper-trading bot. Be skeptical. Only approve
trades where the multi-timeframe technical context aligns with the trade's
direction and there is a clear edge.

Output ONLY a JSON object with these keys:
  - accept: boolean
  - multiplier: number in [0.0, 1.2]   (1.0 = full size; 0 = veto)
  - win_probability: number in [0.0, 1.0]  (rough estimate)
  - expected_r: number  (target_R * p_win - 1 * p_loss)
  - reasoning: short string (under 200 chars) — what convinced you
  - veto_reason: null OR a short string  (populated when accept=false)
  - catalyst: null OR a short string  (any catalyst noted, e.g. earnings)

Hard rules you MUST follow:
- If TV daily rating is STRONG_SELL and the trade is long, set accept=false,
  multiplier=0, veto_reason="long into STRONG_SELL daily".
- If TV daily rating is STRONG_BUY and the trade is short, set accept=false.
- Multiplier never exceeds 1.2.
- Do not invent indicators beyond the data given.
"""


def _build_llm_prompt(
    signal: Any,
    ratings: list[TimeframeRating],
    chart: dict[str, Any],
    baseline: AnalystVerdict,
) -> tuple[str, str]:
    payload = {
        "signal": {
            "symbol": getattr(signal, "symbol", ""),
            "side": getattr(signal, "side", ""),
            "strategy": getattr(signal, "strategy_tag", ""),
            "rule_confidence": float(getattr(signal, "confidence", 0.5)),
            "entry": float(getattr(signal, "entry", 0)),
            "stop": float(getattr(signal, "stop", 0)),
            "target": (
                float(signal.target) if getattr(signal, "target", None) else None
            ),
        },
        "tv_ratings": [asdict(r) for r in ratings],
        "chart": chart,
        "rule_baseline": {
            "accept": baseline.accept,
            "multiplier": baseline.multiplier,
            "expected_r": baseline.expected_r,
            "win_probability": baseline.win_probability,
        },
    }
    user = (
        "Review this trade idea. Output JSON only, no prose:\n\n"
        f"{json.dumps(payload, indent=2)}"
    )
    return _LLM_SYSTEM_PROMPT, user


def _parse_llm_verdict(text: str) -> dict[str, Any] | None:
    """Extract JSON from the LLM response. Tolerant of code fences / prose."""
    if not text:
        return None
    # Try direct parse first.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Strip markdown code fences if present.
    stripped = text.strip()
    for fence in ("```json", "```"):
        if stripped.startswith(fence):
            stripped = stripped[len(fence):].lstrip()
            break
    if stripped.endswith("```"):
        stripped = stripped[:-3].rstrip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        # Last resort: find the outermost { ... }.
        first = stripped.find("{")
        last = stripped.rfind("}")
        if first >= 0 and last > first:
            try:
                return json.loads(stripped[first : last + 1])
            except json.JSONDecodeError:
                return None
    return None
