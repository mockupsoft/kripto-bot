"""Raw-first event ingestion: store verbatim payload, then normalize."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import RawEvent


async def store_raw_event(
    db: AsyncSession,
    source: str,
    event_type: str,
    payload: dict,
    source_timestamp: datetime | None = None,
) -> RawEvent:
    event = RawEvent(
        source=source,
        event_type=event_type,
        payload=payload,
        source_timestamp=source_timestamp,
        received_at=datetime.now(timezone.utc),
        processed=False,
    )
    db.add(event)
    await db.flush()
    return event
