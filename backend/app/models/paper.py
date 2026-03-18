import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, Numeric, SmallInteger, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKey


class PaperOrder(UUIDPrimaryKey, Base):
    __tablename__ = "paper_orders"

    signal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    decision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    market_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    position_group_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    leg_index: Mapped[int] = mapped_column(SmallInteger, default=0)
    is_hedge_leg: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    side: Mapped[str | None] = mapped_column(String(8), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(8), nullable=True)
    order_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    requested_price: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    requested_size: Mapped[float | None] = mapped_column(Numeric(14, 6), nullable=True)
    simulated_delay_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    failure_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        Index(
            "idx_paper_orders_group",
            "position_group_id",
            postgresql_where=(position_group_id.isnot(None)),
        ),
    )


class PaperFill(UUIDPrimaryKey, Base):
    __tablename__ = "paper_fills"

    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    fill_price: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    fill_size: Mapped[float | None] = mapped_column(Numeric(14, 6), nullable=True)
    slippage: Mapped[float | None] = mapped_column(Numeric(8, 6), nullable=True)
    fee: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    fill_quality_score: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    book_snapshot_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    levels_consumed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wavg_fill_price: Mapped[float | None] = mapped_column(Numeric(8, 6), nullable=True)


class PaperPosition(UUIDPrimaryKey, Base):
    __tablename__ = "paper_positions"

    position_group_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    leg_index: Mapped[int] = mapped_column(SmallInteger, default=0)
    is_hedge_leg: Mapped[bool] = mapped_column(Boolean, default=False)
    target_structure: Mapped[str | None] = mapped_column(String(32), nullable=True)

    market_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    strategy: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_wallet_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    side: Mapped[str | None] = mapped_column(String(8), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(8), nullable=True)
    avg_entry_price: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    total_size: Mapped[float | None] = mapped_column(Numeric(14, 6), nullable=True)
    total_cost: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    avg_exit_price: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    realized_pnl: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    unrealized_pnl: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    total_fees: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    total_slippage: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open")
    epoch: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolve_price: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    resolve_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    market_duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        Index("idx_positions_status", "status", opened_at.desc()),
        Index("idx_positions_epoch", "epoch", postgresql_where=(epoch.isnot(None))),
        Index(
            "idx_positions_group",
            "position_group_id",
            postgresql_where=(position_group_id.isnot(None)),
        ),
    )
