import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PositionEvent(Base):
    __tablename__ = "position_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    position_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    price: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    size: Mapped[float | None] = mapped_column(Numeric(14, 6), nullable=True)
    pnl_delta: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    cash_balance: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    position_value: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    total_equity: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    unrealized_pnl: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    realized_pnl_cumulative: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    open_position_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_drawdown: Mapped[float | None] = mapped_column(Numeric(8, 6), nullable=True)
    scenario: Mapped[str] = mapped_column(String(16), default="realistic")
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    config_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("idx_portfolio_time", captured_at.desc()),
        Index("idx_portfolio_run", "run_id", captured_at.desc()),
    )
