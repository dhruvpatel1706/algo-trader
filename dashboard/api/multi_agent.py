"""Multi-agent dashboard endpoints.

Adds the per-agent / per-strategy / alt-data routes used by the multi-agent
dashboard view. Routes here intentionally degrade to empty payloads when a
data source isn't populated yet — the dashboard renders empty states rather
than blowing up with 500s.

Implementation notes:
  * v1 is filesystem-only — backtests/ for backtest history, journal/ for
    fills/signals, in-memory state for live agent status. No database.
  * Where an external data source (insider DB, sentiment cache, wallet
    feeds, governance recommendations) isn't wired up yet, we attempt a
    best-effort lookup and return an empty/neutral payload on any failure.
  * All response models inherit from BaseModel so FastAPI generates an
    OpenAPI schema automatically.
"""

from __future__ import annotations

import json
import logging
import math
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from src.config import PROJECT_ROOT

from dashboard.api.dashboard_metrics import trailing_metrics
from dashboard.api.journal_reader import (
    JOURNAL_DIR,  # noqa: F401 — re-exported so tests can monkeypatch this module's binding
    read_events,
    read_trades,
)
from dashboard.api.state import get_state

log = logging.getLogger(__name__)

router = APIRouter()

BACKTESTS_DIR = PROJECT_ROOT / "backtests"


# ---------------------------------------------------------------------------
# Response models.
# ---------------------------------------------------------------------------


class AgentSummary(BaseModel):
    name: str
    asset_class: str
    state: str
    heat_allocation: float
    coherence: float | None
    n_open_positions: int
    last_eval_ts: str | None


class EquityPoint(BaseModel):
    ts: str
    equity: float


class PortfolioEquityResponse(BaseModel):
    agent: str | None
    days: int
    points: list[EquityPoint]
    start_equity: float | None = None
    end_equity: float | None = None


class LivePosition(BaseModel):
    symbol: str
    qty: int
    avg_entry_price: float
    current_price: float
    unrealized_pl: float
    unrealized_plpc: float
    distance_to_stop: float | None = None
    side: str
    agent: str | None = None


class SignalRecord(BaseModel):
    ts: str
    agent: str | None = None
    strategy: str | None = None
    symbol: str | None = None
    side: str | None = None
    confidence: float | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None


class BacktestRun(BaseModel):
    run_id: str
    ts: str
    sharpe: float | None
    max_dd: float | None
    n_trades: int | None


class CoherenceResponse(BaseModel):
    strategy: str
    coherence: float | None
    live_win_rate: float | None
    backtest_win_rate: float | None
    halted: bool
    halt_reason: str | None


class InsiderTxn(BaseModel):
    ts: str
    filer: str
    role: str
    transaction_code: str
    shares: int
    price_per_share: float
    value: float
    plan_flag: bool
    cluster_flag: bool = False
    repeat_flag: bool = False


class InsiderResponse(BaseModel):
    ticker: str
    transactions: list[InsiderTxn]
    score: float
    warning: str | None = None


class SentimentRecord(BaseModel):
    article_id: str
    ts: str
    headline: str | None
    score: float
    label: str
    confidence: float


class SentimentResponse(BaseModel):
    ticker: str
    hours: int
    rolling_score: float
    items: list[SentimentRecord]
    warning: str | None = None


class WalletFlow(BaseModel):
    ts: str
    wallet: str
    direction: str  # "in" | "out"
    amount_usd: float
    label: str | None = None


class WalletsResponse(BaseModel):
    ticker: str
    hours: int
    flows: list[WalletFlow]
    net_usd: float
    warning: str | None = None


class GovernanceRec(BaseModel):
    target_strategy: str
    action: str
    reason: str
    confidence: float
    ts: str


class GovernanceResponse(BaseModel):
    recommendations: list[GovernanceRec]
    warning: str | None = None


class MoonshotLane(BaseModel):
    name: str
    status: str
    metrics: dict[str, Any] = Field(default_factory=dict)


class MoonshotResponse(BaseModel):
    lanes: list[MoonshotLane]


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _build_agents() -> list[Any]:
    """Construct the default set of trading agents.

    Failure-tolerant: if an agent module won't import we skip it. The dashboard
    cares about presenting *something*, not 500-ing on import drift.
    """
    out: list[Any] = []
    candidates = [
        ("src.agents.equity_agent", "EquityAgent"),
        ("src.agents.gold_agent", "GoldAgent"),
        ("src.agents.bonds_agent", "BondsAgent"),
        ("src.agents.crypto_agent", "CryptoAgent"),
    ]
    for mod_name, cls_name in candidates:
        try:
            mod = __import__(mod_name, fromlist=[cls_name])
            cls = getattr(mod, cls_name)
            out.append(cls())
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("failed to instantiate %s.%s: %s", mod_name, cls_name, exc)
    return out


def _coherence_or_none(val: float) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _read_backtest_runs(strategy: str, days: int) -> list[BacktestRun]:
    """Walk backtests/<strategy>/<run_id>/metrics.json. Return newest-first."""
    strat_dir = BACKTESTS_DIR / strategy
    if not strat_dir.exists():
        return []
    cutoff = datetime.now(UTC) - timedelta(days=days)
    runs: list[BacktestRun] = []
    for run_dir in sorted(strat_dir.iterdir(), reverse=True):
        if not run_dir.is_dir():
            continue
        metrics_path = run_dir / "metrics.json"
        if not metrics_path.exists():
            continue
        try:
            data = json.loads(metrics_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        # Run id like "20260421T142006Z" — parse to datetime if possible.
        run_id = run_dir.name
        run_ts: datetime | None = None
        try:
            run_ts = datetime.strptime(run_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        except ValueError:
            run_ts = None
        if run_ts is not None and run_ts < cutoff:
            continue
        runs.append(
            BacktestRun(
                run_id=run_id,
                ts=run_ts.isoformat() if run_ts else "",
                sharpe=data.get("sharpe"),
                max_dd=data.get("max_dd"),
                n_trades=data.get("n_trades"),
            )
        )
    return runs


def _read_journal_signals(
    limit: int, agent: str | None, since: datetime | None
) -> list[SignalRecord]:
    """Pull signal-flavoured events from the journal.

    Recognises events tagged ``signal``, ``signal_emitted``, or any submit-style
    event that carries a ``confidence`` field. Returns newest-first.
    """
    start = (since.date() if since else date.today() - timedelta(days=30))
    events = read_events(start=start, end=date.today())
    out: list[SignalRecord] = []
    for ev in events:
        kind = ev.get("event")
        is_signal = kind in {"signal", "signal_emitted"} or "confidence" in ev
        if not is_signal:
            continue
        ts_raw = ev.get("ts") or ev.get("timestamp") or ""
        if since is not None and ts_raw:
            try:
                ev_ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                if ev_ts < since:
                    continue
            except ValueError:
                pass
        agent_name = ev.get("agent") or ev.get("strategy_agent")
        if agent and agent_name and agent_name != agent:
            continue
        out.append(
            SignalRecord(
                ts=ts_raw,
                agent=agent_name,
                strategy=ev.get("strategy"),
                symbol=ev.get("subject") or ev.get("symbol"),
                side=ev.get("side"),
                confidence=_coherence_or_none(ev.get("confidence")),
                filters={
                    k: ev[k]
                    for k in ("gap_filter", "news_filter", "ml_filter", "filters")
                    if k in ev
                },
                reason=ev.get("reason"),
            )
        )
    out.sort(key=lambda s: s.ts, reverse=True)
    return out[:limit]


def _coherence_from_journal(strategy: str) -> tuple[float | None, float | None]:
    """Best-effort live win-rate from the journal for a strategy.

    Returns (live_wr, backtest_wr) — both may be None.
    """
    events = read_trades(start=date.today() - timedelta(days=90), end=date.today())
    wins = 0
    losses = 0
    for ev in events:
        if ev.get("strategy") != strategy:
            continue
        pnl = ev.get("pnl")
        if pnl is None:
            continue
        try:
            p = float(pnl)
        except (TypeError, ValueError):
            continue
        if p > 0:
            wins += 1
        elif p < 0:
            losses += 1
    n = wins + losses
    live_wr = (wins / n) if n else None

    # Pull most recent backtest WR.
    runs = _read_backtest_runs(strategy, days=365)
    backtest_wr: float | None = None
    if runs:
        # Re-read the latest metrics file for win_rate (BacktestRun doesn't carry it).
        latest = BACKTESTS_DIR / strategy / runs[0].run_id / "metrics.json"
        try:
            data = json.loads(latest.read_text())
            backtest_wr = data.get("win_rate")
        except (json.JSONDecodeError, OSError):
            backtest_wr = None
    return live_wr, backtest_wr


# ---------------------------------------------------------------------------
# Routes.
# ---------------------------------------------------------------------------


@router.get("/api/agents", response_model=list[AgentSummary])
async def list_agents() -> list[AgentSummary]:
    """Per-agent: name, asset_class, state, heat_allocation, coherence,
    n_open_positions, last_eval_ts."""
    agents = _build_agents()
    out: list[AgentSummary] = []
    for agent in agents:
        try:
            status = agent.status()
            out.append(
                AgentSummary(
                    name=status.name,
                    asset_class=status.asset_class.value
                    if hasattr(status.asset_class, "value")
                    else str(status.asset_class),
                    state=status.state,
                    heat_allocation=status.heat_allocation,
                    coherence=_coherence_or_none(status.coherence),
                    n_open_positions=status.n_open_positions,
                    last_eval_ts=status.last_eval_ts.isoformat()
                    if status.last_eval_ts
                    else None,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("agent %r status() failed: %s", agent, exc)
    return out


@router.get("/api/portfolio/equity", response_model=PortfolioEquityResponse)
async def portfolio_equity(
    agent: str | None = Query(None),
    days: int = Query(90, ge=1, le=3650),
) -> PortfolioEquityResponse:
    """Joined equity curve. If agent= is provided, filter to that agent."""
    start = date.today() - timedelta(days=days)
    events = read_trades(start=start, end=date.today())

    # Best-effort: walk fills, accumulate pnl into a running curve.
    points: list[EquityPoint] = []
    running = 0.0
    by_day: dict[str, float] = defaultdict(float)
    for ev in events:
        if ev.get("event") not in {"fill", "partial_fill"}:
            continue
        if agent and ev.get("agent") != agent:
            continue
        pnl = ev.get("pnl")
        if pnl is None:
            continue
        try:
            by_day[(ev.get("ts") or "")[:10]] += float(pnl)
        except (TypeError, ValueError):
            continue
    for day in sorted(by_day):
        running += by_day[day]
        points.append(EquityPoint(ts=f"{day}T00:00:00+00:00", equity=running))

    return PortfolioEquityResponse(
        agent=agent,
        days=days,
        points=points,
        start_equity=points[0].equity if points else None,
        end_equity=points[-1].equity if points else None,
    )


@router.get("/api/positions/live", response_model=list[LivePosition])
async def positions_live() -> list[LivePosition]:
    """Currently-open positions with live mark, unrealized P&L, distance-to-stop."""
    try:
        from dashboard.api.broker_proxy import get_broker_proxy

        broker = get_broker_proxy()
        raw = broker.get_positions() or []
    except Exception:
        raw = []

    out: list[LivePosition] = []
    for p in raw:
        try:
            current = float(p.get("current_price", 0.0))
            entry = float(p.get("avg_entry_price", 0.0))
            # No formal stop tracking yet; surface a 5% heuristic for the UI.
            stop_est = entry * 0.95 if entry > 0 else 0.0
            distance = (current - stop_est) / current if current else None
            out.append(
                LivePosition(
                    symbol=p["symbol"],
                    qty=int(p.get("qty", 0)),
                    avg_entry_price=entry,
                    current_price=current,
                    unrealized_pl=float(p.get("unrealized_pl", 0.0)),
                    unrealized_plpc=float(p.get("unrealized_plpc", 0.0)),
                    distance_to_stop=distance,
                    side=str(p.get("side", "long")).lower(),
                    agent=p.get("agent"),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


@router.get("/api/signals/recent", response_model=list[SignalRecord])
async def signals_recent(
    agent: str | None = Query(None),
    since: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
) -> list[SignalRecord]:
    """Recent signals with confidence + filter status (gap, news, ml)."""
    since_dt: datetime | None = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            since_dt = None
    return _read_journal_signals(limit=limit, agent=agent, since=since_dt)


@router.get("/api/backtest/history", response_model=list[BacktestRun])
async def backtest_history(
    strategy: str = Query(...),
    days: int = Query(90, ge=1, le=3650),
) -> list[BacktestRun]:
    """Sharpe trend chart data: list of (run_id, ts, sharpe, max_dd, n_trades)."""
    return _read_backtest_runs(strategy, days)


@router.get("/api/coherence", response_model=CoherenceResponse)
async def coherence(strategy: str = Query(...)) -> CoherenceResponse:
    """live_WR / backtest_WR ratio + halt status."""
    state = get_state()
    halt = state.halt_status()
    live_wr, backtest_wr = _coherence_from_journal(strategy)
    coherence_ratio: float | None = None
    if live_wr is not None and backtest_wr and backtest_wr > 0:
        coherence_ratio = live_wr / backtest_wr
    return CoherenceResponse(
        strategy=strategy,
        coherence=coherence_ratio,
        live_win_rate=live_wr,
        backtest_win_rate=backtest_wr,
        halted=bool(halt.get("halted")),
        halt_reason=halt.get("reason"),
    )


@router.get("/api/altdata/insider", response_model=InsiderResponse)
async def altdata_insider(
    ticker: str = Query(...),
    days: int = Query(30, ge=1, le=365),
) -> InsiderResponse:
    """Recent SEC Form 4 buys with cluster/repeat flags."""
    try:
        from src.data.sec_insider import fetch_recent_form4, insider_buy_score

        txns = fetch_recent_form4(tickers=[ticker], days=days)
        # Score-only against the same window.
        score = insider_buy_score(ticker, txns, asof=date.today())

        # Cluster / repeat flags computed from the txn set.
        from collections import Counter

        filer_counts = Counter(t.filer for t in txns if t.transaction_code == "P")
        cluster_window_start = date.today() - timedelta(days=5)
        cluster_filers = {
            t.filer
            for t in txns
            if t.transaction_code == "P" and t.filing_date >= cluster_window_start
        }
        cluster_active = len(cluster_filers) >= 3

        records = [
            InsiderTxn(
                ts=str(t.filing_date),
                filer=t.filer,
                role=t.role,
                transaction_code=t.transaction_code,
                shares=t.shares,
                price_per_share=t.price_per_share,
                value=t.value,
                plan_flag=t.plan_flag,
                cluster_flag=cluster_active and t.filer in cluster_filers,
                repeat_flag=filer_counts.get(t.filer, 0) >= 3,
            )
            for t in txns
        ]
        return InsiderResponse(ticker=ticker, transactions=records, score=score)
    except Exception as exc:
        log.debug("insider lookup unavailable: %s", exc)
        return InsiderResponse(
            ticker=ticker,
            transactions=[],
            score=0.0,
            warning="insider data unavailable",
        )


@router.get("/api/altdata/sentiment", response_model=SentimentResponse)
async def altdata_sentiment(
    ticker: str = Query(...),
    hours: int = Query(24, ge=1, le=24 * 7),
) -> SentimentResponse:
    """Rolling 24h sentiment score per article."""
    try:
        # The sentiment cache lives in Redis at runtime; v1 dashboard returns
        # an empty payload unless a prebuilt cache file exists.
        cache_path = PROJECT_ROOT / "data" / "sentiment_cache" / f"{ticker.upper()}.json"
        if not cache_path.exists():
            return SentimentResponse(
                ticker=ticker,
                hours=hours,
                rolling_score=0.0,
                items=[],
                warning="sentiment cache empty",
            )
        data = json.loads(cache_path.read_text())
        items_in = data.get("items", []) if isinstance(data, dict) else []
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        records: list[SentimentRecord] = []
        scores: list[float] = []
        for it in items_in:
            ts_raw = it.get("ts") or it.get("scored_at") or ""
            try:
                ts_dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                if ts_dt < cutoff:
                    continue
            except ValueError:
                pass
            score = float(it.get("score", 0.0))
            scores.append(score)
            records.append(
                SentimentRecord(
                    article_id=str(it.get("article_id") or it.get("id") or ""),
                    ts=ts_raw,
                    headline=it.get("headline"),
                    score=score,
                    label=str(it.get("label") or "neutral"),
                    confidence=float(it.get("confidence", 0.0)),
                )
            )
        rolling = sum(scores) / len(scores) if scores else 0.0
        return SentimentResponse(
            ticker=ticker, hours=hours, rolling_score=rolling, items=records
        )
    except Exception as exc:
        log.debug("sentiment lookup unavailable: %s", exc)
        return SentimentResponse(
            ticker=ticker,
            hours=hours,
            rolling_score=0.0,
            items=[],
            warning="sentiment data unavailable",
        )


@router.get("/api/altdata/wallets", response_model=WalletsResponse)
async def altdata_wallets(
    ticker: str = Query(...),
    hours: int = Query(168, ge=1, le=24 * 30),
) -> WalletsResponse:
    """Recent smart-money wallet flows. May return empty if NANSEN_API_KEY unset."""
    import os

    if not os.environ.get("NANSEN_API_KEY"):
        return WalletsResponse(
            ticker=ticker,
            hours=hours,
            flows=[],
            net_usd=0.0,
            warning="NANSEN_API_KEY not configured",
        )

    # Wallet feed not implemented in v1 — return empty payload even with creds.
    return WalletsResponse(
        ticker=ticker,
        hours=hours,
        flows=[],
        net_usd=0.0,
        warning="wallet feed not yet wired up",
    )


@router.get("/api/llm/governance", response_model=GovernanceResponse)
async def llm_governance() -> GovernanceResponse:
    """Latest Claude kill/promote/halt recommendations from governance_agent."""
    try:
        from src.agents.governance_agent import GovernanceAgent

        agent = GovernanceAgent()
        # Build a status iterable from currently-active trading agents.
        statuses = []
        for trading_agent in _build_agents():
            try:
                statuses.append(trading_agent.status())
            except Exception:
                logging.getLogger(__name__).warning(
                    "agent.status_failed",
                    extra={"agent": getattr(trading_agent, "name", "?")},
                    exc_info=True,
                )
                continue
        recs = agent.evaluate(statuses) if statuses else []
        out = [
            GovernanceRec(
                target_strategy=r.target_strategy,
                action=r.action,
                reason=r.reason,
                confidence=r.confidence,
                ts=r.ts.isoformat(),
            )
            for r in recs
        ]
        return GovernanceResponse(recommendations=out)
    except Exception as exc:
        log.debug("governance unavailable: %s", exc)
        return GovernanceResponse(recommendations=[], warning="governance unavailable")


@router.get("/api/moonshot/status", response_model=MoonshotResponse)
async def moonshot_status() -> MoonshotResponse:
    """Research-lane progress: HFT sandbox latency, $100->$2M aspirational
    compounding, copy-trading shadow P&L, LLM-discretionary paper P&L."""
    metrics = trailing_metrics()
    metrics_30d = metrics.get("30d", {}) if isinstance(metrics, dict) else {}

    lanes = [
        MoonshotLane(
            name="hft_sandbox",
            status="not_started",
            metrics={
                "p50_latency_us": None,
                "p99_latency_us": None,
                "venue": "simulated",
            },
        ),
        MoonshotLane(
            name="aspirational_compounding",
            status="paper",
            metrics={
                "starting_capital_usd": 100.0,
                "target_usd": 2_000_000.0,
                "current_usd": 100.0,
                "compounding_rate_daily": 0.0,
            },
        ),
        MoonshotLane(
            name="copy_trading_shadow",
            status="not_started",
            metrics={
                "shadow_pnl_30d": metrics_30d.get("total_pnl", 0.0),
                "tracked_wallets": 0,
            },
        ),
        MoonshotLane(
            name="llm_discretionary_paper",
            status="paper",
            metrics={
                "paper_pnl_30d": metrics_30d.get("total_pnl", 0.0),
                "trades_30d": metrics_30d.get("n_trades", 0),
            },
        ),
    ]
    return MoonshotResponse(lanes=lanes)


__all__ = ["router"]
