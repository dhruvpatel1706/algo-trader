# Setup — what you need to start paper trading

This doc is the operator's pre-flight checklist for going from "code lives in the repo" to "bot is running paper trades on real APIs." Compiled in round 4 (2026-05-05) after the demo-data strip, Start/Stop control wiring, and the multi-model LLM router landed.

The bot now refuses to fabricate values when an API isn't connected. Empty states everywhere — see the dashboard at `localhost:3000` to confirm.

---

## TL;DR — minimum to start paper trading

```bash
# 1. Alpaca paper account (free, no deposit needed)
#    → https://app.alpaca.markets/signup
#    → API Keys panel → "Generate New Key" (paper)
ALPACA_API_KEY=PK...
ALPACA_SECRET_KEY=...
ALPACA_PAPER_TRADE=True
LIVE_TRADING=0

# 2. One LLM provider (any of these unblocks sentiment + governance)
ANTHROPIC_API_KEY=sk-ant-...     # primary, claude-haiku-4-5 in router
GEMINI_API_KEY=...               # first fallback, gemini-2.5-flash
OPENAI_API_KEY=sk-...            # second fallback, gpt-4.1-mini

# 3. Local infra (Docker)
docker compose up -d              # Redis + Postgres on host ports 6379/5433

# 4. Start the runner from the dashboard
#    → http://localhost:3000  →  click  "Start"
```

That's the "minimum viable paper bot." Everything below is optional and unlocks specific features when you decide they're worth it.

---

## Tier 1 — required to trade

| Variable | What it is | Where to get it | Cost |
|---|---|---|---|
| `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` | US equities + options + crypto paper trading | [app.alpaca.markets/signup](https://app.alpaca.markets/signup) → paper key | **Free** |
| `ALPACA_PAPER_TRADE` | Hard-coded "True" in v1 | n/a | n/a |
| `LIVE_TRADING` | Must be `0` in v1; both `PaperBroker` and `dashboard/api/kill.py` refuse to run with `LIVE_TRADING=1` | n/a | n/a |
| Redis (port 6379) | Cache, scheduler job store, pub/sub for dashboard WS | `docker compose up -d redis` | Free (local) |
| Postgres (port 5433) | Alt-data persistence (SEC filings, sentiment, wallet trades) | `docker compose up -d db` | Free (local) |

That's it for Tier 1. Without this you don't have a paper bot.

---

## Tier 2 — at least one LLM provider

The bot uses LLMs for sentiment scoring (cheap, ~$5/mo amortized), strategy-scout grading, and the LLM-discretionary moonshot lane. The new **`src/llm/router.py`** routes every call through a fallback chain so a single provider outage doesn't take the bot down. Order of preference is fixed:

1. **Anthropic Haiku 4.5** (primary) — `claude-haiku-4-5-20251001`, cheapest fast model with strong instruction following on classification.
2. **Google Gemini 2.5 Flash** (fallback 1) — `gemini-2.5-flash`, different vendor + region.
3. **OpenAI gpt-4.1-mini** (fallback 2) — `gpt-4.1-mini`, third leg.

You only need ONE of these set. The router skips providers whose key is unset rather than failing. Setting all three buys you resilience against rate limits and regional outages — recommended for production.

| Variable | Pricing (1k input / 1k output tokens, May 2026) |
|---|---|
| `ANTHROPIC_API_KEY` | $0.25 / $1.25 (Haiku 4.5) |
| `GEMINI_API_KEY` | $0.075 / $0.30 (Flash 2.5) |
| `OPENAI_API_KEY` | $0.40 / $1.60 (4.1-mini) |

Operator note from session 2026-05-05: ChatGPT 5.3 Instant and similar models can be slotted into the chain by editing `DEFAULT_CHAIN` in `src/llm/router.py` — the router is provider-agnostic past the dispatch layer. OAuth-based auth would need a per-provider adapter; the current router takes API keys only. Keep "OAuth model bridge" as a v1.1 task if you decide a non-API-key provider is worth wiring up.

---

## Tier 3 — alt-data (optional, each unlocks one feature)

The bot was built so EVERY external integration **gracefully no-ops** without keys. Setting these unlocks specific features:

| Variable | Unlocks | Cost |
|---|---|---|
| `FINNHUB_API_KEY` | Free real-time news sentiment for the ticker bar + news_filter | **Free** (60 req/min) |
| `POLYGON_NEWS_KEY` | Polygon news with full-history backfill (replaces Finnhub when set) | $99/mo |
| `QUIVER_API_KEY` | Congressional trade watchlist boost (`src/data/congress.py`) | $10–50/mo retail tier |
| `NANSEN_API_KEY` | Smart-money wallet shadow-copy for crypto (`src/data/crypto_wallets.py`) | ~$150/mo |
| `POLYGON_STOCKS_KEY` | Full SIP equity feed (replaces Alpaca free IEX-only feed) | $99/mo |
| `POLYGON_OPTIONS_KEY` | Real OPRA options chain + flow (unblocks the wheel/options lane) | $99–199/mo (verify in Polygon dashboard) |
| `DISCORD_WEBHOOK_URL` | Real-time alerts to Discord on warnings/errors | **Free** |
| `COINBASE_API_KEY` + `COINBASE_API_SECRET` | Real Coinbase Advanced paper crypto (replaces SimulatedCryptoBroker) | Free (paper) |
| `ALPACA_CRYPTO_KEY` | Alpaca Crypto paper (alternative to Coinbase) | Free |

### What X / Twitter looks like (operator: this is first-class signal, not noise)

- **X API v2 Basic** — $200/mo, ~10k tweets/mo cap. Worth A/B-testing against existing news pipeline once we have a baseline win rate.
- **Unusual Whales social feed** — bundles X mentions for tickers with options flow data; subscription includes a discord/slack bot.
- **StockTwits API** — free tier is rate-limited; paid is ~$50/mo for unmetered.
- Plan to add `src/data/x_sentiment.py` in a follow-up — same plug-in shape as `src/data/sentiment.py`, anonymize tickers before LLM grading (per Deep90 Reddit pattern).

---

## Tier 4 — paper-trading providers (research summary)

A research agent compared every paper-trading API for a US retail algo bot in May 2026:

| Provider | Paper API | Funding required | Verdict |
|---|---|---|---|
| **Alpaca** (current) | REST + WS, equities + options + crypto | None — email signup only | **Stay here.** Only free, no-deposit, real-time-API paper across all 3 asset classes. |
| Tradier | REST sandbox | None (15-min delayed quotes) | Free secondary cross-check for options order routing. |
| Interactive Brokers | TWS API + Web API | **Live IBKR Pro account must be funded** before paper API works | Skip until ready for live. |
| TradeStation | REST SIM | **Funded brokerage account required** | Skip. |
| tastytrade | Cert sandbox | Mock responses only — not a real fill simulator | Avoid. |
| E*TRADE | REST sandbox | None, but canned responses | Avoid for algo testing. |
| Schwab | **No paper API** | n/a | Avoid. |
| Coinbase Advanced | Sandbox is mocked, not real fills | Free | Use Alpaca Crypto or `SimulatedCryptoBroker` instead. |
| Kraken | REST + local paper engine | None | Optional crypto secondary. |

**Recommendation:** stay on Alpaca for paper. Add Polygon AlgoTrader-Plus-equivalent ($99 stocks, $199 options) ONLY when SIP/OPRA depth becomes a measured backtest blocker. Don't pre-buy.

---

## MCP audit (what's set up vs. what's not)

Checked `~/.claude.json` (global Claude Code config) and `algo-trader/.mcp.json` (repo-scoped):

| MCP | Status | Notes |
|---|---|---|
| `claude.ai Google Drive` | ✓ Connected (global) | Operator's personal MCP, not needed by the bot |
| `claude.ai Gmail` | ✓ Connected (global) | Same |
| `claude.ai Google Calendar` | ! Needs reauth | Same |
| `alpaca` (uvx alpaca-mcp-server) | ✗ Failed to connect | Package installs cleanly via `uvx alpaca-mcp-server`, but fails at startup because `ALPACA_API_KEY`/`ALPACA_SECRET_KEY` aren't in env. Will work once Tier 1 keys are set. |
| **TradingView MCP** | ✗ Does not exist | Not on npm, not in Anthropic MCP registry, not on PyPI. There is no official TradingView MCP. TradingView's chart embeds + Pine Script are GUI-only; no public algo API exists. If you want TradingView signals, the only path is: alerts → webhook → endpoint we'd write. **Not recommended** — the same signals are easier to compute from Alpaca bars. |
| **Polygon MCP** | ✗ Does not exist | Same situation. We use Polygon REST directly via `src/data/loader.py`. |
| **Coinbase MCP** | ✗ Does not exist | Same — REST only. |

**Net:** the only MCP we need for the bot is Alpaca, and it'll work once Tier 1 keys are in `.env`. There's no TradingView MCP to set up because there is no TradingView MCP.

---

## Autonomous LLM reasoner — the trade-decision pivot (round 5)

**No chatbot.** The bot does NOT wait for the operator to type questions. Instead, every candidate signal gets evaluated by an LLM **inside the trade pipeline**, before the existing risk gate sees it.

**Module:** [`src/agents/autonomous_reasoner.py`](src/agents/autonomous_reasoner.py) — one `evaluate(signal_context)` call per candidate signal. Returns:

```python
SignalJudgment(
    multiplier: float,   # CLAMPED to [0.5, 1.2] — never trust raw LLM output
    halt: bool,          # veto vote
    reasoning: str,      # de-anonymized, journaled
    fail_open: bool,     # True when LLM unavailable -> identity behavior
)
```

**Hard rules baked in (with tests pinning each):**

1. Multiplier is clamped into `[0.5, 1.2]`. A hallucinated 50× clamps to 1.2. A negative number clamps to 0.5. NaN/inf → 1.0.
2. The reasoner can dampen signals or veto via `halt=True`. It CANNOT raise position sizing past the risk gate's caps.
3. Tickers are anonymized to `[ASSET_<id>]` placeholders before the LLM sees them (Deep90 anti-bias pattern). De-anonymized in the journaled reasoning.
4. LLM unavailable → `multiplier=1.0, halt=False, fail_open=True`. The rule-based pipeline runs unmodified during outages — a chronic LLM problem must NEVER take the bot down.
5. Every evaluation is journaled with the prompt, raw response, multiplier, reasoning. Auditable end-to-end.

**Wiring:** opt-in per agent via `Agent(reasoner=..., reasoner_context_builder=...)`. When wired, `agent._apply_reasoner(signals)` is called after `generate_signals()` and before the risk gate. The audit trail lives on `agent._last_judgments`.

**Default chain** (from `src/llm/router.py`): Anthropic Haiku 4.5 → Gemini 2.5 Flash → OpenAI gpt-4.1-mini. Skips providers whose keys aren't set. Set ANY ONE to unblock; all three for fallback resilience.

## Gamma exposure (GEX) — regime indicator

**Module:** [`src/signals/gex.py`](src/signals/gex.py). Black-Scholes gamma + SqueezeMetrics-convention dealer GEX aggregation. Tested against synthetic option chains (24 tests) — math is correct independent of any vendor.

```python
from src.signals.gex import compute_dealer_gex, gex_regime_multiplier
summary = compute_dealer_gex(spot=4500.0, chain=option_rows)
# summary.regime is one of: "positive_gamma", "neutral", "negative_gamma"
# multiplier = gex_regime_multiplier(summary.regime)  # 0.65 / 1.0 / 1.15
```

**Production wiring** is gated on `POLYGON_OPTIONS_KEY` — the math is shipped, the chain feed isn't yet. When you sign up Polygon options, write a `fetch_option_chain(ticker)` adapter in `src/data/loader.py` and feed `compute_dealer_gex` from there. Use the multiplier as a confidence input to `macro_regime_filter` and any mean-reversion strategy.

### SKIP

| Xynth feature | Why skip |
|---|---|
| Reddit/Twitter sentiment | **Reconsidered per operator feedback (2026-05-05): X/Twitter is first-class signal, not noise.** Build via Tier 3 X API v2 plug-in, NOT via Xynth wrapper. |
| 5,000-field screener | yfinance + existing fundamentals cover what matters for our universe. Diminishing returns. |
| Politician/insider trades | Already implemented (`src/data/sec_insider.py`, `src/data/congress.py`). |
| Earnings IV-crush automation | Build later; doesn't fit current strategy roster. |
| Trading Automations (their scheduler) | APScheduler runner already does this with promotion gates + correlation alarms. Better. |
| LLM live-code execution | Violates "LLM as governance, not oracle." Hard skip. |

---

## Operating the bot

### From the UI (new in round 4)

The dashboard at `localhost:3000` has a **`BOT STOPPED` / `START`** control in the top bar (next to KILL). Clicking Start spawns `scripts/run_bot.py` as a subprocess inside FastAPI; the runner's stdout streams to `live/runtime/runner.log` and the UI tail viewer. Stop sends SIGTERM with an 8-second grace, escalating to SIGKILL.

The supervisor adopts orphans: if the FastAPI backend reloads while the runner is up, the next page load reads the pidfile, verifies the PID's cmdline matches `run_bot.py`, and re-attaches. **No double-runner risk.**

### From the CLI (still works)

```bash
uv run python scripts/run_bot.py
```

Use this when you want the runner detached from the dashboard process (e.g. running under `launchd` overnight). The dashboard then just observes; Stop is a no-op against runners it didn't start.

### Watchdog

```bash
uv run python scripts/watchdog.py
```

Heartbeat-based dead-man switch. Kill this if you want a clean shutdown.

---

## What's still missing for live capital

The bot is **paper-ready**. Before flipping to live capital, the Phase 9 gates in `docs/live_readiness.md` must pass — paraphrased:

1. ≥6 months forward paper duration
2. Live Sharpe ≥ 0.7× backtest Sharpe
3. ≥150 trades per strategy
4. Slippage MAE ≤ 5 bps vs backtest assumption
5. 0 risk-cap breaches in 90 days
6. Coherence (live_WR/backtest_WR) ≥ 0.5 in last 30 days
7. Pairwise correlation with other live strategies ≤ 0.7

Until those pass, `LIVE_TRADING=0` stays set and `LiveBroker` raises `NotImplementedError`. Both are enforced by validators in `src/config.py` and `src/execution/broker.py`.
