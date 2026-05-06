# Startup Runbook — fire the bot up after a break

When you come back to a clean machine and want to bring everything online,
this is the order. Each step is independent — if you skip one (e.g. you
don't want the watchdog tonight), the rest still work.

## 0. Repo + venv

```bash
cd ~/Desktop/algo-trader
# nothing to install if uv lock is already populated; otherwise:
uv sync
```

## 1. Postgres + Redis (if you want them)

The bot runs without these — Redis falls back to in-memory APScheduler;
Postgres only matters when alt-data ingestion lands. Skip unless you
already have docker-compose set up for this repo.

```bash
docker compose up -d postgres redis  # only if compose file is configured
```

## 2. Backend (FastAPI / uvicorn)

```bash
cd ~/Desktop/algo-trader
uv run uvicorn dashboard.api.main:app --host 0.0.0.0 --port 8000 --reload
```

What you should see at boot:

- `INFO: Uvicorn running on http://0.0.0.0:8000`
- `INFO: Application startup complete.`

If you've been running an older copy of this code: stop the prior uvicorn
first. Stale process = stale code, stale broker proxy cache, stale qty
truncation.

Quick sanity check from another terminal:

```bash
curl -s http://localhost:8000/api/feeds/status | python3 -m json.tool | head -30
```

You should see ``configured: true`` for Alpaca, Gemini, OpenAI, Finnhub,
Anthropic, Polygon news. Anything you've added to ``.env`` since the last
restart shows up here on the next refresh.

## 3. Frontend (Next.js)

```bash
cd ~/Desktop/algo-trader/dashboard/web
npm run dev
```

Open http://localhost:3000. The "connected feeds" chip strip on the
Portfolio view shows what's wired. Live positions and the equity bar
refetch every 5–10s automatically (no manual refresh needed).

## 4. Bot (the actual trading runner)

Either click **Start** in the dashboard top bar (POSTs to ``/api/bot/start``)
or run it manually:

```bash
cd ~/Desktop/algo-trader
PYTHONPATH=. uv run python scripts/run_bot.py
```

Manual mode is useful when you want to see the logs in your terminal.
The dashboard mode persists better (PID adoption survives backend
restarts).

What you should see in the journal within ~5 min of starting:

```bash
tail -f journal/$(date -u +%Y-%m-%d).jsonl
```

Expect: ``heartbeat`` every 15s, ``data_refresh`` every 60s during NYSE
hours, ``crypto_data_refresh`` every 5 min 24/7, plus ``agent_eval_complete``
records each time an agent runs (every 5 min for equity-class, 15 min
for crypto, 1 hour for governance).

## 5. Watchdog (optional but recommended for overnight)

Sibling process. Pulls ``/api/bot/status`` every 5 min, classifies health,
auto-restarts the bot on crash. Has zero broker access.

```bash
cd ~/Desktop/algo-trader
PYTHONPATH=. uv run python scripts/watchdog_agent.py
```

Logs go to ``live/watchdog/agent_<date>.jsonl`` and stdout. If the bot
crashes mid-night, the watchdog will see it as ``state=crashed`` after
60s of grace and POST ``/api/bot/start``.

## 6. Heartbeat dead-man (extra safety, optional)

The simplest watchdog of all — flattens the book if the bot's heartbeat
goes stale. Belt + suspenders alongside ``watchdog_agent.py``.

```bash
PYTHONPATH=. uv run python scripts/watchdog.py
```

## What "everything is healthy" looks like

Top bar (refreshing every 5s):

- ``BOT RUNNING`` (green), uptime ticking
- ``EQUITY $99,xxx``, ``CASH $xxx``, ``B.POWER $xxx`` — moving on each refresh when crypto bars tick
- ``DAY P&L`` updating with the position
- ``LAST EVAL`` = a timestamp within the last few minutes

Live positions table:

- ``LIVE MARK`` shows fresh quote-mid for crypto
- ``MARK AGE`` shows ``Xs ago`` ticking up every second (means the page
  is alive even if the price hasn't moved)
- Hovering the age cell shows the source: ``latest_quote_mid`` for
  crypto, ``position_snapshot`` for equity

Live trades panel:

- Pulls from ``/api/trades`` which reads ``journal/<date>.jsonl``
- Should show every ``trade_submit`` event the runner has emitted

## When something looks wrong

| Symptom | Likely cause | Fix |
|---|---|---|
| qty shows 3 instead of 3.99 | uvicorn running stale code | Restart uvicorn |
| live trades panel empty despite a real fill | journal_reader filter mismatch (older code) | Restart uvicorn |
| live mark frozen for many minutes | Alpaca paper crypto data tier ticks slowly; mark age chip should still tick | Normal — watch the mark_age column to confirm freshness |
| Anthropic shows ✗ in feeds bar despite being in .env | shell has stale empty ANTHROPIC_API_KEY shadowing .env | Restart uvicorn (it now does ``load_dotenv(override=True)``) |
| dashboard says BOT STOPPED but PID file exists | supervisor adopted an orphan with mismatched cmdline | Manually kill the orphan PID, then ``/api/bot/start`` |

## Real-time refresh cadences

| Surface | Cadence | Bound by |
|---|---|---|
| TopBar portfolio | 5s | react-query refetchInterval |
| TopBar halt | 10s | react-query refetchInterval |
| LivePositionsTable | 5s | react-query refetchInterval |
| LiveTradesFeed | 5s | react-query refetchInterval |
| FeedsStatusBar | 30s | react-query refetchInterval |
| Mark age tick (no network) | 1s | client-side ``setInterval`` |
| BrokerProxy cache | 3s | server-side TTL |
| Crypto latest_quote | 3s | server-side TTL on the same proxy |

The 5s frontend + 3s backend cache means each render is at most 8s old.
