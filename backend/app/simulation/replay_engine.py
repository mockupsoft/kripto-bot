"""Deterministic and stochastic replay engine.

Same seed = same stochastic outcomes (deterministic mode).
Different seeds = variation for robustness testing (stochastic mode).
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.event_normalizer import process_pending_events
from app.ingestion.mock_adapter import MockEventReplay
from app.models.strategy import ReplaySession


async def start_replay(
    db: AsyncSession,
    name: str,
    start_time: datetime,
    end_time: datetime,
    speed_multiplier: float = 1.0,
    scenario: str = "realistic",
    random_seed: int | None = None,
    is_deterministic: bool = True,
) -> ReplaySession:
    """Create and start a replay session."""
    session = ReplaySession(
        name=name,
        start_time=start_time,
        end_time=end_time,
        speed_multiplier=speed_multiplier,
        scenario=scenario,
        random_seed=random_seed or random.randint(1, 2**31),
        is_deterministic=is_deterministic,
        status="running",
    )
    db.add(session)
    await db.flush()
    return session


async def run_replay(
    db: AsyncSession,
    session_id: UUID,
) -> dict:
    """Execute a replay session."""
    session = await db.get(ReplaySession, session_id)
    if not session:
        return {"error": "session not found"}

    # Set up RNG with seed for deterministic replay
    if session.is_deterministic and session.random_seed:
        random.seed(session.random_seed)

    adapter = MockEventReplay(speed_multiplier=float(session.speed_multiplier))

    event_count = await adapter.replay(
        db=db,
        start_time=session.start_time,
        end_time=session.end_time,
    )

    processed = await process_pending_events(db)

    session.status = "completed"
    session.results = {
        "events_replayed": event_count,
        "events_processed": processed,
        "seed_used": session.random_seed,
    }
    await db.flush()

    return session.results
