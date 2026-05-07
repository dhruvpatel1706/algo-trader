# Morning Summary — 2026-05-07 (overnight session)

**TL;DR:** Bot is up, healthy, and now running with all overnight fixes applied (cumulative cap, LLM cooldown, new EMA Ribbon strategy). 5 over-cap crypto positions from before the fix are still open and will only resolve when their exits fire — that's expected, not a bug. New dashboard panel surfaces what the parallel researcher session is recommending.

---

## What landed tonight (12 commits)

| Commit | What | Why |
|---|---|---|
| `0a429ba` | Equity chart fix | Chart was hugging the x-axis on freshly-funded accounts |
| `1e621e7` | Crypto universe widened to 11 majors, cadence to 5 min | More opportunities, faster cycle |
| `06fe87f` | Supervisor false-CRASHED chip fix | Re-adopt orphan PID on every status() call |
| `bedc7bd` | **Cumulative cap fix** | Per-symbol cap now considers existing notional. Stops the "ETH stacked to 27%" failure mode. |
| `af6153c` | Multi-Claude orchestration layer | File-based locks + handoff briefs for parallel sessions |
| `dc3d2ea` | Live positions get correct `agent` attribution | Was showing `null` after restart |
| `6b73734` | `% equity` column in LivePositionsTable | BREACH / AT CAP / near cap chips |
| `f9dc3c5` | Orchestrator surfaces out-of-spec session outputs | Postel's law on read |
| `d6b3d8c` | Funding rate Bybit + OKX fallbacks | Binance HTTP 451 was killing funding ingest |
| `83b67bc` | **LLM router cooldown** | 5-min skip on per-provider failure. Stopped 180 dead-provider calls/hour during the outage. |
| `4da1b1a` | **EMA Ribbon Compression Breakout strategy** | Researcher session's #1 pick from `docs/improvements/strategies/`. 8 unit tests, registered to `crypto_majors`. |
| `e1f1f81` | **Research proposals dashboard panel** | Surfaces parallel researcher's queue + watchlist + top confluence without context-switching to a worktree |

---

## Current bot state (as of restart at 23:35 ET)

- **PID:** check `live/runtime/runner.pid`
- **Equity:** $99,487.58 (started at $100k, day -$512 / -0.51%)
- **Cash:** $317.64 (most equity in positions, expected)
- **Open positions:** 5 (all crypto, all `crypto_agent`)
- **Agents armed:** equity, gold, silver, bonds, crypto, governance (6 total)
- **Jobs scheduled:** 14

### Position concentration (per cumulative cap fix)

| Symbol | Notional | % equity | Status |
|---|---|---|---|
| ETHUSD | $37,028 | **37.22%** | **BREACH (3.7× over cap)** |
| LTCUSD | $19,892 | **20.00%** | **BREACH (2× over cap)** |
| BCHUSD | $16,644 | 16.73% | over cap |
| AVAXUSD | $15,592 | 15.67% | over cap |
| DOGEUSD | $9,998 | 10.05% | at cap |

**These were stacked before the cumulative-cap fix landed (commit bedc7bd at 22:52).** The fix is now active — new signals on these symbols will be rejected at the risk gate. Existing over-cap positions resolve only when their exits trigger (stops or targets).

If you want to manually trim ETH/LTC down to spec, the dashboard kill switch flattens everything; partial trims are safer done via Alpaca's web UI directly. I left them alone because (a) you're paper, (b) they're not catastrophic — current unrealized PnL is -$275 across the 5, drawdown well within cap.

### LLM router status

All 3 providers (Gemini quota, Anthropic billing, OpenAI quota) were hitting limits before sleep. Cooldown is now active so we stop wasting cycles on dead providers. **Action for you:** when you wake up, top up at least one provider (Gemini Studio is free tier, Anthropic and OpenAI are paid) so the reasoner has a working chain. Until then the reasoner falls open with multiplier=1.0 — strategies still trade, just without LLM confidence scaling.

---

## What's running in parallel sessions

The orchestrator panel + new research proposals panel show real-time state. Quick read:

- **watcher** — staleness `stale` (last update 02:51 UTC, cadence 15 min). Likely the watcher session paused or its lock cleaned up; check the dashboard.
- **researcher** — staleness `fresh` (last update 02:53 UTC, cadence 4h). Wrote `researcher_brief.json` with 4 strategy proposals + watchlist (DOGE, ETH, SOL).
- **backtester** — locked, PID 52658, fresh. Running.
- **improver** — never written. Either the session hasn't been started yet or it hasn't produced output.
- **operator** — fresh. The original operator brief from 02:52 still surfaces.

**Researcher's queue:**

| Rank | Strategy | Status |
|---|---|---|
| 1 | `ema_ribbon_compression_breakout` | **shipped** ✅ |
| 2 | `funding_rate_divergence` | proposed (needs Bybit fallback — done in commit d6b3d8c, can implement) |
| 3 | `vwap_nyse_open_retest` | proposed (requires 5m bars; loader supports it) |
| 4 | `on_chain_whale_flow` | proposed (needs new `src/data/onchain.py`) |

**Researcher's watchlist (next session):**
- DOGEUSDT — confluence 0.59, RSI 28.81 (oversold), ADX 38.64 (strong trend). Triggers `confluence ≥ 0.70` if MACD histogram crosses zero from below.
- ETHUSDT — confluence 0.45, RSI 29.92, ADX 22.01. Triggers if ADX expands above 25 with RSI <35 and price near lower BB.
- SOLUSDT — confluence 0.0, ADX 27.43. Triggers if RSI drops below 40 OR rallies above 60 (directional agreement) while ADX holds above 27.

---

## Known issues to look at when you wake up

1. **Over-cap positions** — see table above. Decide whether to manually trim ETH/LTC or let stops do it.
2. **LLM providers all exhausted** — top up Gemini (free tier) at minimum so reasoner has a working chain.
3. **Watcher session stale** — either restart it or kill the lock and let the orchestrator skip the role until you have time.
4. **Improver session never produced output** — orchestrator UI shows it as stale. Either the session is the wrong worktree or it hasn't started a cycle.
5. **Strategies API only shows 2 strategies** (`mr_etf`, `wheel_etf`) — that endpoint reads a different registry than the agents do. Cosmetic UI issue, not a runtime issue. The crypto_agent IS running its full strategy list.

---

## Tests

Full unit suite: **1312 passed, 1 skipped** (the skip is the optional native module). Lint clean across all changed files.

8 new tests for EMA Ribbon Compression. 4 new tests for the research proposals endpoint.

---

## Live verification (after 23:35 restart)

First crypto_agent eval ran at **23:39:54** with the new code. Result from the journal:

```json
{"event":"autonomous_reasoner_eval","symbol":"DOGEUSDT",...,
 "judgment":{"multiplier":1.0,"halt":false,
 "reasoning":"LLM unavailable (... openai in cooldown (300s remaining))",
 "fail_open":true}}

{"event":"refusal","reason":"risk_cap_position","symbol":"DOGEUSDT",
 "detail":"symbol already at single-position cap:
  existing 10029.602911 >= 10% of equity 99487.58"}

{"event":"agent_eval_complete","agent":"crypto_agent",
 "n_signals":5,"n_submitted":0,"n_refused":5}
```

**Both overnight fixes verified live:**
- ✅ **Cumulative cap (`bedc7bd`)** — 5/5 signals correctly refused; bot can no longer stack on top of over-cap positions.
- ✅ **LLM cooldown (`83b67bc`)** — circuit breaker active; reasoner fails open with multiplier=1.0 instead of crashing.

---

## Quick links

- Dashboard: http://localhost:8000 (or your port)
- Bot log: `live/runtime/runner.log`
- Today's journal: `journal/2026-05-07.jsonl`
- New strategy: `src/strategies/ema_ribbon_compression.py`
- Research proposals API: `GET /api/orchestrator/research_proposals`
