import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKey


class Market(UUIDPrimaryKey, Base):
    __tablename__ = "markets"

    polymarket_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    condition_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    question: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    slug: Mapped[str | None] = mapped_column(String(256), nullable=True)
    outcomes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    token_ids: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fees_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    fee_rate_bps: Mapped[int] = mapped_column(default=0)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_markets_active", "is_active", "category"),
        Index("idx_markets_condition", "condition_id"),
    )


class MarketRelationship(UUIDPrimaryKey, Base):
    __tablename__ = "market_relationships"

    market_a_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    market_b_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    relationship_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    asset_symbol: Mapped[str | None] = mapped_column(String(16), nullable=True)
    description: Mapped[str | None] = mapped_column(String(256), nullable=True)
    normal_spread_mean: Mapped[float | None] = mapped_column(Numeric(8, 6), nullable=True)
    normal_spread_std: Mapped[float | None] = mapped_column(Numeric(8, 6), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("market_a_id", "market_b_id"),
        Index("idx_market_rel_active", "is_active", "asset_symbol"),
    )


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    market_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    raw_event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    best_bid: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    best_ask: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    midpoint: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    spread: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    bid_depth: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    ask_depth: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    book_levels: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_trade_price: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    volume_24h: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    open_interest: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="poll")

    __table_args__ = (
        Index("idx_snapshots_market_time", "market_id", captured_at.desc()),
    )
