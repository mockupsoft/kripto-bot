"""WebSocket client for Polymarket market channel."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import websockets

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.ingestion.event_store import store_raw_event

settings = get_settings()


class PolymarketWSClient:
    def __init__(self, asset_ids: list[str] | None = None):
        self._url = settings.POLYMARKET_WS_URL
        self._asset_ids = asset_ids or []
        self._ws = None
        self._running = False

    async def connect_and_listen(self, db_factory) -> None:
        self._running = True
        while self._running:
            try:
                async with websockets.connect(self._url) as ws:
                    self._ws = ws
                    await self._subscribe(ws)
                    await self._listen_loop(ws, db_factory)
            except (websockets.ConnectionClosed, ConnectionError):
                if self._running:
                    await asyncio.sleep(2)

    async def _subscribe(self, ws) -> None:
        if not self._asset_ids:
            return
        sub = {
            "assets_ids": self._asset_ids,
            "type": "market",
            "custom_feature_enabled": True,
        }
        await ws.send(json.dumps(sub))
        asyncio.create_task(self._heartbeat(ws))

    async def _heartbeat(self, ws) -> None:
        while self._running:
            try:
                await ws.send("PING")
                await asyncio.sleep(10)
            except Exception:
                break

    async def _listen_loop(self, ws, db_factory) -> None:
        async for message in ws:
            if message == "PONG":
                continue
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                continue

            event_type = data.get("event_type", "unknown")
            source_ts = None
            if data.get("timestamp"):
                try:
                    source_ts = datetime.fromtimestamp(int(data["timestamp"]) / 1000, tz=timezone.utc)
                except (ValueError, TypeError):
                    pass

            async with db_factory() as db:
                await store_raw_event(db, "polymarket_ws", event_type, data, source_timestamp=source_ts)
                await db.commit()

    def stop(self) -> None:
        self._running = False
