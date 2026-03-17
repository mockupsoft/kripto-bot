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

    # Exit engine — execution layer tuning (see 00-known-issues.mdc)
    STALE_SNAPSHOT_HOURS: float = 4.0     # was 2h — prediction markets need patience; 4–6h for test
    STALE_SOFT_GUARD: bool = False        # if True: don't force-close on stale, only block new entries
    STALE_EXIT_DISABLED: bool = False     # 24h test: disable stale_data exits entirely
    STOP_LOSS_PCT: float = 0.10           # was 0.08 — "edge var ama sabır yok"; 10% gives more room
    STALE_MARKET_BLACKLIST: str = ""     # comma-separated market UUIDs; from top_markets_by_stale_count


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


def get_stale_market_blacklist(settings: Settings) -> frozenset[str]:
    """Parse STALE_MARKET_BLACKLIST into set of market IDs for fast lookup."""
    raw = (settings.STALE_MARKET_BLACKLIST or "").strip()
    if not raw:
        return frozenset()
    return frozenset(m.strip() for m in raw.split(",") if m.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
