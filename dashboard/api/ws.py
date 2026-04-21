"""WebSocket fan-out from the Redis bus to dashboard clients.

Subscribes to: agent.event, order.*, fill.*, pnl.tick, risk.halt, kill.
30s heartbeat keeps proxies happy.
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
)


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
                await ws.send_json({"channel": str(msg.get("channel")), "data": data})
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
