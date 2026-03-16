"""Mock adapter for replaying seeded events without external connectivity."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import RawEvent


class MockEventReplay:
    """Replays raw_events in chronological order, simulating real-time ingestion.

    Used for offline development and demo scenarios.
    """

    def __init__(self, speed_multiplier: float = 1.0):
        self._speed = speed_multiplier
        self._running = False

    async def replay(
        self,
        db: AsyncSession,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        on_event=None,
    ) -> int:
        """Replay events from raw_events table, calling on_event for each.

        Marks replayed events as unprocessed so the normalizer can re-process them.
        """
        q = select(RawEvent).order_by(RawEvent.received_at.asc())
        if start_time:
            q = q.where(RawEvent.received_at >= start_time)
        if end_time:
            q = q.where(RawEvent.received_at <= end_time)

        result = await db.execute(q)
        events = result.scalars().all()

        self._running = True
        replayed = 0
        prev_time: datetime | None = None

        for event in events:
            if not self._running:
                break

            if prev_time and event.received_at and self._speed > 0:
                delta = (event.received_at - prev_time).total_seconds()
                wait = delta / self._speed
                if wait > 0 and wait < 60:
                    await asyncio.sleep(wait)

            if on_event:
                await on_event(event)

            event.processed = False
            prev_time = event.received_at
            replayed += 1

        await db.flush()
        return replayed

    def stop(self) -> None:
        self._running = False
