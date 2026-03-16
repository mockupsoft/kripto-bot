import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKey


class StrategyRun(UUIDPrimaryKey, Base):
    __tablename__ = "strategy_runs"

    strategy: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    config_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")


class ReplaySession(UUIDPrimaryKey, Base):
    __tablename__ = "replay_sessions"

    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    speed_multiplier: Mapped[float] = mapped_column(Numeric(5, 2), default=1.0)
    scenario: Mapped[str | None] = mapped_column(String(16), nullable=True)
    random_seed: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_deterministic: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    results: Mapped[dict] = mapped_column(JSONB, default=dict)
