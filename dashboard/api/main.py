"""FastAPI dashboard backend.

Routes:
  GET  /api/portfolio              — equity + cash + buying power + day change
  GET  /api/positions              — open positions
  GET  /api/orders?status=&limit=  — recent orders
  GET  /api/trades?from=&to=       — historical trades from journal
  GET  /api/strategies             — strategy on/off + paused_at
  GET  /api/metrics                — 30/90/365d trailing metrics from journal
  GET  /api/halt                   — current halt status
  GET  /api/costs                  — LLM token + API request counter
  GET  /api/agent-events           — recent agent activity (ring buffer)
  GET  /api/incidents              — past kill incidents
  POST /api/strategies/{name}/pause
  POST /api/strategies/{name}/resume
  POST /api/halt/reset             — clear a manual halt
  POST /api/kill                   — body: {"confirm": "FLATTEN", "reason": "..."}
  WS   /ws/stream                  — fan-out of bus events
  GET  /api/health                 — liveness
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from dashboard.api import ws as ws_module
from dashboard.api.broker_proxy import BrokerProxy, get_broker_proxy
from dashboard.api.dashboard_metrics import trailing_metrics
from dashboard.api.journal_reader import read_trades
from dashboard.api.kill import execute_kill, list_incidents
from dashboard.api.state import DashboardState, get_state

log = logging.getLogger(__name__)

app = FastAPI(title="algo-trader dashboard", version="0.1.0")

# CORS for local dev (Next.js on :3000 → FastAPI on :8000).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class KillRequest(BaseModel):
    confirm: str = Field(..., description='must equal "FLATTEN"')
    reason: str = Field(default="manual kill from dashboard", max_length=200)


_StateDep = Annotated[DashboardState, Depends(get_state)]
_BrokerDep = Annotated[BrokerProxy, Depends(get_broker_proxy)]


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "service": "algo-trader-dashboard"}


@app.get("/api/portfolio")
def portfolio(broker: _BrokerDep) -> dict:
    acc = broker.get_account()
    if acc is None:
        return {"connected": False, "reason": "alpaca credentials not configured"}
    return {"connected": True, **acc}


@app.get("/api/positions")
def positions(broker: _BrokerDep) -> list[dict]:
    return broker.get_positions() or []


@app.get("/api/orders")
def orders(
    broker: _BrokerDep,
    status_: str = Query("all", alias="status"),
    limit: int = Query(50, ge=1, le=500),
) -> list[dict]:
    return broker.get_orders(status=status_, limit=limit) or []


@app.get("/api/trades")
def trades(
    from_: date | None = Query(None, alias="from"),
    to: date | None = Query(None),
) -> list[dict]:
    return read_trades(start=from_, end=to)


@app.get("/api/strategies")
def strategies(state: _StateDep) -> list[dict]:
    return state.list_strategies()


@app.post("/api/strategies/{name}/pause")
def pause_strategy(name: str, state: _StateDep) -> dict:
    if not state.pause(name):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"strategy '{name}' not found")
    return {"name": name, "enabled": False}


@app.post("/api/strategies/{name}/resume")
def resume_strategy(name: str, state: _StateDep) -> dict:
    if not state.resume(name):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"strategy '{name}' not found")
    return {"name": name, "enabled": True}


@app.get("/api/halt")
def halt(state: _StateDep) -> dict:
    return state.halt_status()


@app.post("/api/halt/reset")
def halt_reset(state: _StateDep) -> dict:
    state.reset_halt()
    return state.halt_status()


@app.get("/api/metrics")
def metrics() -> dict:
    return trailing_metrics()


@app.get("/api/costs")
def costs(state: _StateDep) -> dict:
    return state.costs()


@app.get("/api/agent-events")
def agent_events(state: _StateDep, limit: int = Query(50, ge=1, le=500)) -> list[dict]:
    return state.recent_agent_events(limit=limit)


@app.get("/api/incidents")
def incidents(limit: int = Query(50, ge=1, le=500)) -> list[dict]:
    return list_incidents(limit=limit)


@app.post("/api/kill")
def kill(req: KillRequest, broker: _BrokerDep, state: _StateDep) -> dict:
    if req.confirm != "FLATTEN":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, 'kill requires {"confirm":"FLATTEN"}')
    incident = execute_kill(broker, state, reason=req.reason, requested_by="dashboard")
    return {"ok": True, **incident}


@app.websocket("/ws/stream")
async def ws_stream(ws: WebSocket) -> None:
    await ws_module.stream(ws)
