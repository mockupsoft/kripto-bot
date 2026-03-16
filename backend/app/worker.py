"""arq worker entry point."""

import os

# Read REDIS_URL from env before any other imports (get_settings/.env must not override)
redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

from arq import run_worker
from arq.connections import RedisSettings

from app.config import assert_demo_mode, get_settings
from app.ingestion.jobs import WorkerSettings

if __name__ == "__main__":
    settings = get_settings()
    assert_demo_mode(settings)
    run_worker(WorkerSettings, redis_settings=RedisSettings.from_dsn(redis_url))
