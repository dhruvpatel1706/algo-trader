"""WebSocket fan-out from the Redis bus to dashboard clients.

Subscribes to the original trading channels plus the multi-agent extension
channels. Every channel is mapped to a friendly ``type`` so the frontend can
switch on ``event.type`` without parsing the channel name.

Channels:
  agent.event       — generic agent activity
  order.*           — order lifecycle (submit/fill/reject/cancel)
  fill.*            — fill events  -> type "fill"
  pnl.tick          — portfolio P&L heartbeat
  risk.halt         — strategy halted -> type "halt_event"
  kill              — emergency flatten
  signal.*          — new signals fired -> type "signal"
  coherence.alert   — strategy crossed threshold -> type "coherence_alert"

A 30s heartbeat keeps proxies happy.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator

from fastapi import WebSocket, WebSocketDisconnect
from src.agents.bus import Bus

log = logging.getLogger(__name__)

_CHANNELS = (
    "agent.event",
    "order.*",
    "fill.*",
    "pnl.tick",
    "risk.halt",
    "kill",
    # Multi-agent extensions:
    "signal.*",
    "coherence.alert",
)


def _classify(channel: str) -> str:
    """Map a Redis channel name to a friendly event type for the UI."""
    if channel.startswith("signal"):
        return "signal"
    if channel.startswith("fill"):
        return "fill"
    if channel == "coherence.alert":
        return "coherence_alert"
    if channel == "risk.halt":
        return "halt_event"
    if channel.startswith("order"):
        return "order"
    if channel == "kill":
        return "kill"
    if channel == "agent.event":
        return "agent_event"
    if channel == "pnl.tick":
        return "pnl_tick"
    return "message"


async def _heartbeat(ws: WebSocket) -> None:
    while True:
        await asyncio.sleep(30)
        with contextlib.suppress(Exception):
            await ws.send_json({"type": "heartbeat", "ts": asyncio.get_event_loop().time()})


async def _stream_events(ws: WebSocket) -> AsyncIterator[None]:
    bus = Bus()
    try:
        async with bus.subscribe(*_CHANNELS) as pubsub:
            async for msg in pubsub.listen():
                if msg.get("type") != "pmessage" and msg.get("type") != "message":
                    continue
                payload = msg.get("data")
                if isinstance(payload, str):
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        data = {"raw": payload}
                else:
                    data = {"raw": str(payload)}
                channel = str(msg.get("channel"))
                await ws.send_json(
                    {"type": _classify(channel), "channel": channel, "data": data}
                )
                yield None
    finally:
        await bus.close()


async def stream(ws: WebSocket) -> None:
    """Bidirectional websocket: bus events out, optional pings in."""
    await ws.accept()
    hb = asyncio.create_task(_heartbeat(ws))
    try:
        async for _ in _stream_events(ws):
            pass
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("ws stream error")
    finally:
        hb.cancel()
        with contextlib.suppress(Exception):
            await ws.close()
