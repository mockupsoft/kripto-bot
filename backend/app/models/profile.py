from sqlalchemy import Boolean, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKey


class LatencyProfile(UUIDPrimaryKey, Base):
    __tablename__ = "latency_profiles"

    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detection_delay_ms: Mapped[int] = mapped_column(Integer, default=500)
    decision_delay_ms: Mapped[int] = mapped_column(Integer, default=200)
    execution_delay_ms: Mapped[int] = mapped_column(Integer, default=300)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class SlippageProfile(UUIDPrimaryKey, Base):
    __tablename__ = "slippage_profiles"

    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    base_slippage_bps: Mapped[int] = mapped_column(Integer, default=20)
    volatility_multiplier: Mapped[float] = mapped_column(Numeric(5, 2), default=1.5)
    depth_factor: Mapped[float] = mapped_column(Numeric(5, 2), default=0.5)
    use_book_walking: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class RiskProfile(UUIDPrimaryKey, Base):
    __tablename__ = "risk_profiles"

    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    starting_balance: Mapped[float] = mapped_column(Numeric(14, 2), default=900.00)
    max_position_pct: Mapped[float] = mapped_column(Numeric(5, 4), default=0.08)
    max_total_exposure_pct: Mapped[float] = mapped_column(Numeric(5, 4), default=0.20)
    max_correlated_positions: Mapped[int] = mapped_column(Integer, default=2)
    consecutive_loss_cooldown: Mapped[int] = mapped_column(Integer, default=5)
    kelly_fraction: Mapped[float] = mapped_column(Numeric(5, 4), default=0.25)
    daily_loss_stop_pct: Mapped[float] = mapped_column(Numeric(5, 4), default=0.10)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
