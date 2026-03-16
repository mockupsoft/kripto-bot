"""REST polling for Polymarket public APIs."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.ingestion.event_store import store_raw_event
from app.ingestion.rate_limiter import RateLimitedClient

settings = get_settings()


class PolymarketRestPoller:
    def __init__(self) -> None:
        self.client = RateLimitedClient(requests_per_second=4.0)
        self.gamma_url = settings.POLYMARKET_GAMMA_API
        self.clob_url = settings.POLYMARKET_CLOB_API
        self.data_url = settings.POLYMARKET_DATA_API

    async def poll_markets(self, db: AsyncSession, limit: int = 50) -> int:
        resp = await self.client.get(f"{self.gamma_url}/markets", params={"limit": limit, "active": "true"})
        markets = resp.json()
        count = 0
        for m in markets:
            await store_raw_event(db, "polymarket_rest", "market_discovery", m)
            count += 1
        return count

    async def poll_wallet_trades(self, db: AsyncSession, wallet_address: str, limit: int = 100) -> int:
        resp = await self.client.get(
            f"{self.data_url}/trades",
            params={"user": wallet_address, "limit": limit},
        )
        trades = resp.json()
        count = 0
        for t in trades:
            source_ts = None
            if t.get("timestamp"):
                try:
                    source_ts = datetime.fromtimestamp(int(t["timestamp"]) / 1000, tz=timezone.utc)
                except (ValueError, TypeError):
                    pass
            await store_raw_event(db, "polymarket_rest", "trade", t, source_timestamp=source_ts)
            count += 1
        return count

    async def poll_order_book(self, db: AsyncSession, token_id: str) -> None:
        resp = await self.client.get(f"{self.clob_url}/book", params={"token_id": token_id})
        book = resp.json()
        source_ts = None
        if book.get("timestamp"):
            try:
                source_ts = datetime.fromtimestamp(int(book["timestamp"]) / 1000, tz=timezone.utc)
            except (ValueError, TypeError):
                pass
        await store_raw_event(db, "polymarket_rest", "book", book, source_timestamp=source_ts)

    async def poll_midpoint(self, db: AsyncSession, token_id: str) -> None:
        resp = await self.client.get(f"{self.clob_url}/midpoint", params={"token_id": token_id})
        data = resp.json()
        await store_raw_event(db, "polymarket_rest", "price_change", data)

    async def close(self) -> None:
        await self.client.close()
