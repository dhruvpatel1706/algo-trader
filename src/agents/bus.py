"""Async Redis pub/sub bus.

Channels:
  agent.event   — every subagent run emits one
  order.*       — submit, fill, partial_fill, reject, cancel
  pnl.tick      — aggregated portfolio P&L heartbeat
  risk.halt     — emitted when a halt fires
  kill          — emergency flatten + halt-all
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as aredis

from src.config import get_settings


class Bus:
    def __init__(self, url: str | None = None) -> None:
        self._url = url or get_settings().REDIS_URL
        self._client: aredis.Redis | None = None

    async def connect(self) -> None:
        if self._client is None:
            self._client = aredis.from_url(self._url, decode_responses=True)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def publish(self, channel: str, payload: dict) -> int:
        await self.connect()
        assert self._client is not None
        return await self._client.publish(channel, json.dumps(payload, default=str))

    @asynccontextmanager
    async def subscribe(self, *channels: str) -> AsyncIterator[aredis.client.PubSub]:
        await self.connect()
        assert self._client is not None
        pubsub = self._client.pubsub()
        await pubsub.subscribe(*channels)
        try:
            yield pubsub
        finally:
            await pubsub.unsubscribe(*channels)
            await pubsub.aclose()
