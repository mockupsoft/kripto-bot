"""arq job definitions for periodic polling and event processing."""

from __future__ import annotations

import os

from arq.connections import RedisSettings

from app.dependencies import async_session_factory
from app.ingestion.event_normalizer import process_pending_events


def _redis_settings() -> RedisSettings:
    """Use REDIS_URL from environment so Docker Compose can set redis://redis:6379/0."""
    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return RedisSettings.from_dsn(url)


async def normalize_events_job(ctx: dict) -> int:
    async with async_session_factory() as db:
        count = await process_pending_events(db)
        await db.commit()
        return count


async def startup(ctx: dict) -> None:
    pass


async def shutdown(ctx: dict) -> None:
    pass


class WorkerSettings:
    functions = [normalize_events_job]
    on_startup = startup
    on_shutdown = shutdown
    cron_jobs = []
    queue_name = "polybot"
    max_jobs = 10
    job_timeout = 300
    redis_settings = _redis_settings()
