import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKey


class TradeSignal(UUIDPrimaryKey, Base):
    __tablename__ = "trade_signals"

    strategy: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_wallet_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    market_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    related_market_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    relationship_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    side: Mapped[str | None] = mapped_column(String(8), nullable=True)
    model_probability: Mapped[float | None] = mapped_column(Numeric(8, 6), nullable=True)
    model_confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    market_price: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    raw_edge: Mapped[float | None] = mapped_column(Numeric(8, 6), nullable=True)
    net_edge: Mapped[float | None] = mapped_column(Numeric(8, 6), nullable=True)
    spread_z_score: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    costs_breakdown: Mapped[dict] = mapped_column(JSONB, default=dict)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)

    __table_args__ = (
        Index("idx_signals_time", created_at.desc()),
        Index("idx_signals_strategy", "strategy", created_at.desc()),
    )


class SignalDecision(UUIDPrimaryKey, Base):
    __tablename__ = "signal_decisions"

    signal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)

    signal_age_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detection_to_decision_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    price_at_decision: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    price_drift: Mapped[float | None] = mapped_column(Numeric(8, 6), nullable=True)
    spread_at_decision: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)

    kelly_fraction: Mapped[float | None] = mapped_column(Numeric(8, 6), nullable=True)
    proposed_size: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    available_bankroll: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    current_exposure_pct: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)

    edge_at_signal: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
    edge_at_decision: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
    edge_erosion_pct: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)

    scenario: Mapped[str] = mapped_column(String(16), default="realistic")
    latency_profile_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    slippage_profile_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    __table_args__ = (
        Index("idx_decisions_signal", "signal_id"),
    )
