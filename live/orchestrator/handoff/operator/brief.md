# Operator Handoff Brief — Equity-focused Restart

**Written by:** ultraplan session (Sonnet 4.6, main worktree)
**Date:** 2026-05-07T18:10 UTC
**Status:** Bot restarted post-flatten. Regime filter + concentration alarm wired. LLM credits exhausted (top-up needed).

---

## What changed in this session

### Wiring landed (uncommitted, in working tree)

| Change | File | Why |
|---|---|---|
| Regime filter as exposure scalar | `src/runtime/trade_pipeline.py` | Every leaderboard row fails per-window std/|mean| ≤ 0.50 (2.7-9.5x). The structural fix is regime-conditional sizing, not new strategies. Mean-reversion suppressed in `risk_on`, trend-following suppressed in chop, failed-breakout dampened in extreme regimes. |
| `regime_dampened` refusal threshold @ 0.4 | `src/runtime/trade_pipeline.py` | Below 0.4 = "wrong-regime, should not fire". Above 0.4 = dampen confidence. |
| Pre-cycle concentration alarm @ 30% | `src/runtime/trade_pipeline.py` + `src/risk/concentration.py` | The 10% per-symbol cap blocks NEW entries; this catches drift on already-held positions. Emits `cap_breach_alert` in journal but does not halt. The 13h ETH @ 37% breach on 2026-05-07 had no first-class alarm event. |
| `regime_scalar` + `regime_label` fields on `ExecutionStep` | `src/runtime/trade_pipeline.py` | Surfaces regime context in journal + dashboard for every signal. |

The `pf_concentration` metric, `concentration.py`, `regime_scalar.py` modules, billing-aware LLM router, and `regime_dampened` refusal taxonomy were ALREADY landed in commit `00ef41b` from a parallel session (synthesized identically — both Claudes converged). My work is the *wiring layer* on top.

### Bot restarted
- Killed by operator at 15:35 UTC ("rebuild analyst, stop bleed"), all 5 positions flatten-realized -$2,160.50 (-2.16%).
- Restarted at 18:06 UTC via `POST /api/bot/start {"confirm":"START"}`.
- Equity $97,735.81 cash, 0 positions. Equity_agent should fire its first eval at ~18:11 UTC during NYSE RTH.
- Pipeline will tag every signal's `regime_scalar` + `regime_label` in the journal so the watcher's next digest can trace which signals were dampened by which regime.

### Critical: LLM is fail-open across all 3 providers
| Provider | Status | Action needed |
|---|---|---|
| Anthropic | "Your credit balance is too low" — billing | Top up at https://console.anthropic.com/settings/billing |
| Gemini | Free-tier 20 RPD exceeded | Resets at 00:00 UTC OR upgrade to paid |
| OpenAI | `insufficient_quota` — billing | Top up at https://platform.openai.com/usage |

The router correctly detects billing errors (commit `00ef41b`'s `_is_billing_error()`) and emits `CRITICAL: ALL providers failed`. **Bot runs rule-only until at least one provider has credits.** This means the autonomous reasoner contributes no judgment until top-up. The mechanical strategies (mr_etf, ma_pullback_trend, failed_breakout, vwap_open_retest) trade their pure rules through the risk gate.

---

## What other sessions are working on (do NOT collide)

| Session | Likely owner | Where they're writing | Status as of now |
|---|---|---|---|
| Researcher | crypto-confluence scout | `live/orchestrator/researcher_brief.json` | S5 done, no triggers fired today, watching DOGE BB%B + MACD cross |
| Backtester | nightly walk-forward | `live/orchestrator/backtests/leaderboard.json` | Done 15:26 UTC; next scheduled 2026-05-08T04:00Z. Top 3: mr_etf 0.79 / momentum_xs 0.75 / ma_pullback_trend 0.66 — all reject/marginal on per-window std |
| Watcher | health digests | `live/orchestrator/watcher/2026-05-07-*.md` | Latest cycle 11 @ 17:48 UTC. Will see bot=running on next cycle (16:05+) |
| **Analyst-builder** | Pre-trade TV-multiTF analyst | `src/agents/analyst.py` (untracked), `src/runtime/trade_pipeline.py` (uncommitted) | **Active, mid-flight**. Wired `analyst: Any = None` into TradePipeline + analyst_veto refusal path + analyst_multiplier hook (currently unused — wiring incomplete). 4 lint errors in their file (3× RUF100 unused noqa, 1× RUF003 × character). Don't touch their code; they'll finish it. |

---

## Invariants honored

- Risk caps in `src/config.py` UNCHANGED: 1% per-trade · 6% portfolio heat · 10% single position · -2% daily halt · -15% drawdown halt.
- `LIVE_TRADING="0"` — paper-only. Alpaca paper account.
- Every regime + concentration event is journaled. No hidden state.
- All 1360 unit tests pass. Lint clean for my files; analyst.py has 4 errors that are theirs to clean up.

---

## What I did NOT do

- Did NOT push the 56-58 unpushed commits to origin (operator decision).
- Did NOT commit my wiring changes — analyst code is uncommitted in the same working tree from a parallel session, mixing them in one commit would be sloppy. Will commit cleanly once analyst session lands.
- Did NOT touch the analyst code (lint errors + dead variable are theirs).
- Did NOT modify mr_etf or wheel_etf (operator-halted at flatten time; restart re-enables them by default — fresh process clears in-memory halts).
- Did NOT trim ETH (operator already flatten-resolved this incident).

---

## Recommended next operator actions

1. **Top up Anthropic credits** (cheapest fix to restore reasoner — Haiku 4.5 is the secondary chain link).
2. **Watch the journal for `cap_breach_alert` events** — none should fire today since portfolio is empty, but next time a position drifts past 30% it'll be a first-class event.
3. **After 30+ trades have a `regime_scalar` field**, ask the backtester session to recompute walk-forward leaderboard with the regime scalar applied; per-window std should compress.
4. **Before tomorrow's backtester run**, decide whether to commit my regime/concentration wiring — clean diff, all tests green, but mixed with parallel session's uncommitted analyst work in `trade_pipeline.py`.

---

## Files I touched (uncommitted)

```
src/runtime/trade_pipeline.py    — regime + concentration wiring (mine) + analyst hooks (parallel session)
src/risk/regime_scalar.py        — typography revert (— → -, ≤ → <=, × → x) for lint
src/journal/refusal_events.py    — already at HEAD (parallel session and I both added regime_dampened)
src/backtest/metrics.py          — already at HEAD (parallel session added pf_concentration)
src/risk/concentration.py        — already at HEAD (parallel session added; my version was identical)
```

---

## How to read this brief

The orchestrator polls operator/brief.md every 4h cadence. The watcher will see:
- Bot transitioned crashed → running
- New journal events resuming
- Concentration check fires once per cycle (currently 0 breaches because flat)
- Regime scalar appears on each signal (1.0 when SPY-bars missing or asset_class=crypto)

If you see ETH or any single symbol > 30%, expect a `cap_breach_alert` event AND a watcher digest flag. That's working as designed.

*Next operator update: when LLM credits restored OR significant trading activity observed.*
