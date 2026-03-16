import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKey


class WalletTransaction(UUIDPrimaryKey, Base):
    __tablename__ = "wallet_transactions"

    wallet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    market_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    raw_event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    detection_lag_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    side: Mapped[str | None] = mapped_column(String(8), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(8), nullable=True)
    price: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    size: Mapped[float | None] = mapped_column(Numeric(14, 6), nullable=True)
    notional: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_sequence_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        Index("idx_wallet_tx_wallet", "wallet_id", detected_at.desc()),
        Index("idx_wallet_tx_market", "market_id", detected_at.desc()),
        Index("idx_wallet_tx_occurred", "wallet_id", occurred_at.desc()),
        Index(
            "idx_wallet_tx_dedup",
            "source_sequence_id",
            unique=True,
            postgresql_where=(source_sequence_id.isnot(None)),
        ),
    )
