import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKey


class Wallet(UUIDPrimaryKey, Base):
    __tablename__ = "wallets"

    address: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    proxy_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_tracked: Mapped[bool] = mapped_column(Boolean, default=False)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class WalletScore(UUIDPrimaryKey, Base):
    __tablename__ = "wallet_scores"

    wallet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    total_roi: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    hit_rate: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
    avg_position_size: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    avg_hold_time_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_drawdown: Mapped[float | None] = mapped_column(Numeric(16, 4), nullable=True)
    market_diversity_score: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
    concentration_penalty: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
    consistency_score: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
    suspiciousness_score: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
    classification: Mapped[str | None] = mapped_column(String(32), nullable=True)
    copyability_score: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
    composite_score: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
    copy_decay_curve: Mapped[dict] = mapped_column(JSONB, default=dict)
    explanation: Mapped[dict] = mapped_column(JSONB, default=dict)

    __table_args__ = (
        UniqueConstraint("wallet_id", "scored_at"),
        Index("idx_wallet_scores_composite", composite_score.desc()),
        Index("idx_wallet_scores_wallet", "wallet_id", scored_at.desc()),
    )
