# Analyst Rebuild — 2026-05-07

## Why this exists

Overnight (2026-05-06 → 05-07), the bot opened 5 long positions across
crypto majors (AVAXUSD, BCHUSD, DOGEUSD, ETHUSD, LTCUSD), all sized
beyond the cumulative-cap envelope, and held them into a coordinated
crypto sell-off. Realised P&L on the unwind: **−$2,264 / −2.26%** of
account equity. Final cash: $97,735.81. Zero positions, bot halted, no
auto-trading.

The cause was structural, not a bad day in the market:

1. **No independent confirmation of trend.** `ma_pullback_trend_crypto`
   emits "buy" on a 200-SMA pullback. The bot trusted the rule with no
   second source. Every one of the 5 unwound trades was a long into a
   coin that read SELL or worse on TradingView's daily.
2. **Reasoner was a single-multiplier rubber stamp.** The old
   `autonomous_reasoner` returned one number in [0, 1]. There was no
   "veto"; nothing checked whether the trend agreed with the trade.
3. **No catalyst awareness.** A trade like DDOG (which the operator
   pointed out — STRONG_BUY across timeframes the day after an earnings
   beat) looked identical in priority to "long ETH on a falling daily."

The rebuild replaces the rubber stamp with a structured **Analyst** —
multi-timeframe ratings → hard veto → soft sizing dampener → optional
LLM synthesis with bounded fallback.

---

## What's in the analyst

`src/agents/analyst.py`

### Inputs per signal
- The strategy `Signal` (symbol, side, entry, stop, target, confidence,
  strategy_tag, asset_class)
- The existing `SignalContext` (recent_bars are summarised compactly)
- TradingView analyst summary at **3 timeframes**: 1D, 4H, 1H.
  Sourced from the public `tradingview-ta` library; no API key.

### Decision pipeline
1. **Route the symbol to a screener/exchange** (crypto/BINANCE for
   `*USDT` and `*USD`, america/NASDAQ for equities, with a per-symbol
   override map for edge cases).
2. **Fetch ratings** at all 3 timeframes. Resilient to TV outages —
   per-timeframe failures are logged and the analyst proceeds with
   whatever succeeded. A 5-min in-memory TTL cache prevents re-hammering
   TV when multiple agents/strategies evaluate the same symbol.
3. **Hard-veto check.** Refuse the trade outright when:
   - long signal on a symbol whose 1D TV rating is `STRONG_SELL`, OR
   - long signal on a symbol whose 4H TV rating is `STRONG_SELL`.
   Veto is deterministic, journalled with `refusal_reason="analyst_veto"`,
   and survives LLM outages.
4. **Multiplier sizing** (no veto):
   - aligned-score weighted across 1D (0.5) / 4H (0.3) / 1H (0.2)
   - aligned ≥ 1.5 → 1.20× (boost — clear trend agreement)
   - aligned ≥ 0.5 → 1.00× (full size)
   - aligned ≥ −0.5 → 0.80× (proceed but smaller)
   - else → 0.50× (signal is fading a confirmed trend; cut size in half)
5. **Probability proxy.** `p_win` = blend of (a) strategy's rule
   confidence and (b) TV's bullish-vote ratio on the top timeframe in
   the signal direction, clipped to [0.05, 0.95]. Used to compute
   `expected_R = target_R × p_win − 1 × (1 − p_win)`.
6. **Optional LLM synthesis.** When the LLM router is available, the
   analyst sends the rule baseline + structured TV ratings to the model
   and asks for a JSON verdict. Hallucinated multipliers are clamped to
   [0, 1.2]. On any router failure (cooldown, parse error, billing) the
   analyst keeps the rule-based verdict and tags the journal entry with
   `source="rules-llm-failed"` so the operator can see when AI was down.

### Output: `AnalystVerdict`
Every field is journal-safe:
```
accept              : bool                     — hard gate
multiplier          : float in [0, 1.2]         — sizing knob
win_probability     : float in [0.05, 0.95]     — rough proxy
expected_r          : float                     — +EV when > 0
reasoning           : str                       — operator-readable trace
veto_reason         : str | None
timeframe_ratings   : list[TimeframeRating]
catalyst            : str | None                — populated by v2 layer
source              : "llm" | "rules" | "rules-llm-failed"
```

---

## How it composes with existing safety

The analyst sits **before** the reasoner in `TradePipeline._process_signal`:

```
strategy.signal
    → ANALYST.evaluate         (this PR)
        accept=False  → refuse, journal, halt this signal
        accept=True   → multiplier_a in [0.5, 1.2]
    → reasoner.judge           (existing)
        halt=True     → refuse, journal
        halt=False    → multiplier_r in [0, 1]
    → applied_multiplier = multiplier_a × multiplier_r
    → applied_confidence = applied_multiplier × signal.confidence × regime_scalar
    → risk + sizing + cumulative cap (untouched)
    → broker.submit
```

Existing protections that stay enforced exactly as they were:
- Cumulative-cap cooldown using `MAX(mark, book)` (no ratchet)
- LLM-cooldown circuit breaker per provider
- All `src/risk/limits.py` validators (1% per trade, 6% portfolio heat)
- Real-capital ladder validator in `src/config.py`
- `/api/kill` flatten-all endpoint with `confirm: "FLATTEN"`

---

## Reasoning samples on today's actual signals

These are the analyst's verdicts on the 6 crypto pairs and DDOG, using
TradingView ratings captured at 11:46 ET on 2026-05-07. Identical to
what the live runner will produce when restarted.

### Boost case — DDOG (the operator's catalyst case)
```
ACCEPT    DDOG   buy   mult=1.20  <- BOOST   p_win=63%   E[R]=+0.90
TV: 1D=STRONG_BUY(16/1/9) | 4H=STRONG_BUY(17/0/9) | 1H=BUY(14/4/8)
failed_breakout buy on DDOG. Aligned score +1.80 → multiplier 1.20.
```
**What this means:** every timeframe agrees (post-earnings momentum is
intact), so the analyst sizes UP to 1.2× the rule baseline. This is the
"datadog just went crazy up probably earnings call" pattern the
operator wanted.

### Dampen case — ETH/BCH (the overnight pattern that bled the account)
```
ACCEPT    ETHUSDT   buy   mult=0.50  <- DAMP   p_win=37%   E[R]=+0.11
TV: 1D=SELL(5/11/10) | 4H=SELL(2/14/10) | 1H=SELL(2/15/9)
```
**What this means:** all 3 timeframes read SELL. Not a STRONG_SELL on
1D, so no hard veto fires under current rules — but the analyst halves
the size. If the trade goes wrong, the loss is ~50% of what would have
happened with the old single-multiplier path.

> **Open question for v2:** should "majority SELL across all timeframes"
> escalate to a hard veto, even when none is STRONG_SELL? The cost
> of erring on the side of caution: skip some real long entries during
> deep pullbacks. The cost of NOT escalating: nights like 2026-05-06.
> I'd recommend tightening the veto after we observe one full week of
> live behaviour.

### Mixed case — BTC / AVAX / DOGE (mostly bearish lower TFs)
```
ACCEPT    BTCUSDT   buy   mult=0.80  <- DAMP   p_win=51%   E[R]=+0.52
TV: 1D=BUY(12/6/8) | 4H=SELL(7/10/9) | 1H=SELL(4/13/9)
```
Daily is bullish, but 4H + 1H are bearish — the analyst takes the trade
at 80% size. Smaller than full, larger than the all-SELL case.

### Clean trend case — LTC
```
ACCEPT    LTCUSDT   buy   mult=1.00            p_win=51%   E[R]=+0.52
TV: 1D=BUY(12/5/9) | 4H=BUY(12/5/9) | 1H=SELL(5/11/10)
```
Both higher timeframes agree (1D + 4H = BUY); the 1H sell is just the
intraday counter-trend. Full-size.

---

## What's wired and what's not

### Done (this PR)
- `src/agents/analyst.py` (529 lines) — the analyst module
- `tests/unit/agents/test_analyst.py` (18 tests, all passing) — covers
  veto, boost, dampen, routing, LLM synthesis, parser tolerance,
  serialisation
- `pyproject.toml` — `tradingview-ta>=3.3.0` added as a runtime dep
- `src/runtime/trade_pipeline.py` — analyst step inserted before
  reasoner; refusal path emits `refusal_reason="analyst_veto"` and
  attaches all TV ratings to the journal entry; multiplier composes via
  `applied_multiplier = analyst_multiplier × reasoner_multiplier`
- `scripts/run_bot.py` — `_build_analyst()` factory; passes the same
  LLM router the autonomous reasoner uses (shared cooldowns); analyst
  threaded through `_build_pipeline()`
- TV fetcher resilience: multi-exchange fallback chain
  (BINANCE → KUCOIN → BYBIT → MEXC for crypto;
  NASDAQ → NYSE → AMEX for equities), 429-aware (does not burn the
  chain on rate-limit), 5-minute in-memory TTL cache

### Not yet built (deliberate v2 follow-on)
- **Catalyst layer** (yfinance earnings calendar + recent headline
  count). Plug-in slot already exists on `AnalystVerdict.catalyst`. This
  is the layer that would write "DDOG earnings beat 24h ago" into the
  journal. Estimated 1-2 hours; needs separate review since yfinance is
  rate-limited and we don't want it to block analyst evaluation.
- **Tighter veto** (3-of-3 SELL → veto). Park this until we have a
  week of live observation — see the open question above.
- **Backtest replay of the analyst overlay.** Useful but expensive —
  the rule-based path can be replayed deterministically against the
  past 2 years of bars + cached TV ratings; the LLM path can't. I'd
  recommend running the deterministic side over 2024-2025 before
  promoting the analyst to "trusted" — the multiplier scaling could
  underperform on certain regime types we haven't seen.

---

## Restart criteria — DO NOT just unhalt

Before lifting the halt and letting the bot trade live again:

1. **Operator reviews this doc and the sample verdicts above.** If any
   of the 6 multiplier outputs look wrong, fix the rule before restart.
2. **Add a paper pre-flight day.** Restart the bot at *0.25× normal
   risk* (`RISK_PER_TRADE_PCT` halved twice) for 24 hours and watch the
   journal. Confirm:
   - Analyst veto fires at least once on a real bearish-trend signal
   - Multiplier composition reaches the broker (`applied_multiplier`
     present in journal entries)
   - No exceptions in the analyst path during a full crypto cycle
3. **Tighten the veto only after** the first week of live data shows
   the analyst is too lenient on all-SELL setups (operator decision).
4. **Catalyst layer is NOT a restart blocker** — the analyst is
   already a real upgrade without it.

If any of those gates fail, halt again and iterate. The LLM router
cooldown means even if all 3 providers go down mid-day, the analyst
keeps working on the deterministic rule path — losing AI synthesis is
not equivalent to losing the analyst.

---

## What this does NOT promise

The user asked for "at least 30% returns." A multi-timeframe trend
filter + R:R-aware sizing is an honest improvement on what was running.
It is not, by itself, a 30% strategy. Realistic expectation:

- The analyst's primary effect is **smaller losses on bad signals**
  (size 0.5× instead of 1.0×), not bigger wins. Drawdown reduction is
  the highest-confidence outcome.
- Sharpe should improve by 0.1–0.3 from the dampening alone.
- 30% returns will require the catalyst + news + alt-data lanes (Phases
  3 + 5 of the master plan), plus continued strategy walk-forward
  evaluation, plus the multi-agent diversification.

The analyst is the spine the rest of those layers will plug into. It's
not the destination.
