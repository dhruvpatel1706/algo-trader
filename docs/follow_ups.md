# Follow-ups (post-Round 8 + watchdog)

Living checklist of decisions/items that surfaced while wiring the live
pipeline, in priority order. Tonight's overnight run is the validation gate
for everything below.

---

## 1. Retire `/Users/dhruvpatel/Desktop/trading-bot/`

**Status:** kept on disk for now (not deleted). Inventory survey done
2026-05-06.

**Decision:** trading-bot is **not** worth merging wholesale into algo-trader.
6,581 LOC; ~70% duplicates a weaker version of what algo-trader already has,
~20% scaffolds features algo-trader has consciously deferred (RL, full
options, HFT), ~5–10% is genuinely novel.

**Port these 3 things only (~210 LOC):**

| trading-bot file | algo-trader destination | Notes |
|---|---|---|
| `src/trading_bot/intelligence/news_feeds/twitter.py` (90 LOC) | `src/data/social_feeds.py` (new) | Twitter/X bearer-token polling. Aligns with saved memory: "X/Twitter is a first-class alpha source." Rewrite to feed `src/data/news.py`'s `NewsArticle` shape and route through existing `score_article` LLM scorer (don't ship trading-bot's FinBERT path). |
| `src/trading_bot/intelligence/news_feeds/reddit.py` (74 LOC) | `src/data/social_feeds.py` (new) | asyncpraw scrape. Same shape as Twitter ingest. |
| `src/trading_bot/intelligence/sentiment/entity_extractor.py` (47 LOC) | `src/data/sentiment.py` (extend) | Ticker extraction utility used by both feeds above. |
| `src/trading_bot/intelligence/prediction/event_impact.py` table only (~30 lines of data) | `docs/event_impact.yaml` (new config asset) | Keyword→expected-return table; load it into the autonomous reasoner's context, not as code. |

**Skip everything else.** Specifically:
- All of `trading_bot/learning/` (PPO RL, online learner) — premature complexity, fights algo-trader's LightGBM `src/ml/` filter approach.
- All of `trading_bot/options/` — algo-trader's `wheel_etf.py` stub + `risk/option_limits.py` already encodes the v1 deferral plus a roadmap (IVR, delta, assignment) for when options data lands.
- All of `trading_bot/hft/` — algo-trader explicitly defers HFT to v2 with a hard `LIVE_BROKER_BRIDGE: bool = False` invariant in `src/moonshot/hft_sandbox.py`.
- `trading_bot/core/{event_bus, engine}` — algo-trader is journal-driven + scheduler-driven, not event-bus-driven. Architectural mismatch.

**Action:** after the 3-port is done, we can `rm -rf /Users/dhruvpatel/Desktop/trading-bot/` (NOT under git so a hard delete is the correct action; it's also outside this repo so doesn't pollute history).

---

## 2. Wire `tradingview-mcp` into the runner

**Repo:** [`atilaahmettaner/tradingview-mcp`](https://github.com/atilaahmettaner/tradingview-mcp). MIT, 2,318 stars, last pushed 2026-05-05. Provides 30+ TA indicators, multi-exchange screening (Binance/KuCoin/Bybit), news sentiment (Reddit + RSS), backtesting helpers — all over MCP, no API keys required.

**Why it fits here:** today our autonomous reasoner sees only the rule-confidence number. With tradingview-mcp on the same MCP bus as the reasoner's Claude/Gemini call, the LLM can fetch live RSI/Bollinger/screening data on demand before issuing a multiplier. That's a meaningful upgrade in judgment quality at zero new ongoing cost.

**Why it's deferred:** tonight's overnight run validates Round 8's signal-to-broker pipeline. Adding a new MCP surface mid-flight increases failure modes. Wire it AFTER tomorrow morning's review.

**Plan:**

1. Install: `uv add tradingview-mcp-server` (or run via `uvx`).
2. New file: `src/llm/mcp_tools.py` — thin client that exposes the relevant tradingview-mcp tools (`get_indicators`, `screen_symbols`, `get_news_sentiment`) as Python functions the reasoner can call.
3. Extend `src/agents/autonomous_reasoner.py` so when an LLM provider supports tool-use (Anthropic + OpenAI do, Gemini does function-calling), the prompt includes the available tools and the eval loop honors them.
4. Add timing instrumentation — a tradingview-mcp call adds ~200-500ms; if it pushes total reasoner latency above ~2s the eval window starts missing 5-min cadences. Hard timeout at 1.5s with fallback to the no-tools path.
5. Tests: mock the MCP transport, unit-test that tool calls round-trip cleanly and that tool-unavailable degrades to the existing path.

---

## 3. LLM-decision-loop / discretionary trading lane

**Status:** plan moonshot lane (paper-only, gated). Defer until tonight's run validates.

**What the user is asking for:** following the OpenProphet / JakeNesler "I gave Claude Code 100k" pattern — Claude Code itself running as the trading brain on a heartbeat loop, not just multiplying confidence on rule signals.

**How it would fit here without breaking the safety architecture:**

- New `src/agents/discretionary_claude.py` — a separate agent class registered alongside equity/gold/bonds/crypto/governance.
- Heartbeat: phased like OpenProphet (pre-market 15m, market open 2m, midday 10m, close 2m, after-hours 30m, closed 1h). Lives in `src/runtime/calendar.py`.
- Each beat spawns a `claude-code` subprocess with:
  - tradingview-mcp (TA data)
  - the bot's own MCP that exposes `get_journal_tail`, `get_positions`, `get_account`, `propose_trade(symbol, side, qty, reasoning)` — but NOT direct broker access
  - System prompt = `docs/discretionary_rules.md` (auditable, version-controlled)
- `propose_trade` is a tool that submits the proposal back through the SAME `TradePipeline.run_for` we already have — meaning the reasoner, risk gate, and broker are unchanged. The Claude Code instance can suggest a trade; risk caps still gate it; OutcomeCapture still records it; coherence is still tracked per the gate criteria.
- Hard rule: this lane writes `agent="discretionary_claude"` on every journal entry, never bypasses `src/risk/limits.py`, never reaches the live broker codepath. Promotion to live follows the same Phase 9 gate as every other strategy.

**Why we're not doing it tonight:** introducing autonomous Claude Code while the wired-pipeline is hours old triples the failure-mode surface. First validate that the rules agents trade cleanly through the night; build this Friday/Saturday.

---

## 4. Tonight's overnight run plan

Sequence:

1. Restart uvicorn (`make dev` or whatever's in OPERATIONS.md).
2. POST `/api/bot/start` (or click Start in dashboard).
3. In another terminal: `PYTHONPATH=. uv run python scripts/watchdog_agent.py` — this is the LLM watchdog, runs every 5 min, restarts the bot if it crashes.
4. Optionally: `PYTHONPATH=. uv run python scripts/watchdog.py` — this is the heartbeat dead-man (flattens on no-pulse). Belt + suspenders.
5. Watch `live/journal/2026-05-07.jsonl` and `live/watchdog/agent_2026-05-07.jsonl` in the morning.

What I expect to see by sunrise:
- `crypto_agent.eval` ran every 15 min × ~10 hours = ~40 evals, with some submitted orders (BTC + ETH on Alpaca paper).
- `gold_agent` / `bonds_agent` / `equity_agent` evals all gated (NYSE closed) until 09:30 ET.
- Watchdog journal: 12 cycles/hour × 10 hours = ~120 verdicts, all `health=good` if nothing broke.
- No `runner_crashed` events.

If anything goes wrong: the watchdog will either auto-restart (on crash) or escalate to `health=critical` in its journal with a concern + advisory list. That gives us a clean diagnosis to act on in the morning.
