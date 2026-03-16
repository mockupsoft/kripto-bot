from __future__ import annotations

import hashlib
import json
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DEMO_MODE_ONLY: bool = True

    DATABASE_URL: str = "postgresql+asyncpg://polybot:polybot_dev@localhost:5432/polybot"
    DATABASE_URL_SYNC: str = "postgresql://polybot:polybot_dev@localhost:5432/polybot"
    REDIS_URL: str = "redis://localhost:6379/0"

    POLYMARKET_GAMMA_API: str = "https://gamma-api.polymarket.com"
    POLYMARKET_CLOB_API: str = "https://clob.polymarket.com"
    POLYMARKET_DATA_API: str = "https://data-api.polymarket.com"
    POLYMARKET_WS_URL: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

    POLL_INTERVAL_SECONDS: int = 10
    SNAPSHOT_INTERVAL_SECONDS: int = 5
    WALLET_POLL_INTERVAL_SECONDS: int = 10

    BACKEND_HOST: str = "0.0.0.0"
    BACKEND_PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"


class DemoModeViolation(RuntimeError):
    """Raised when any code path attempts real-money operations."""


def assert_demo_mode(settings: Settings) -> None:
    if not settings.DEMO_MODE_ONLY:
        raise DemoModeViolation(
            "DEMO_MODE_ONLY must be True. This system is designed for paper trading only. "
            "Real-money execution is architecturally blocked."
        )


def compute_config_hash(config: dict) -> str:
    canonical = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


@lru_cache
def get_settings() -> Settings:
    return Settings()
