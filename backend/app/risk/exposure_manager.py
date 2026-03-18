"""Exposure management: caps, correlated exposure, daily stop."""

from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.paper import PaperPosition
from app.models.portfolio import PortfolioSnapshot

# Global hard caps — configurable via env for experiments
MAX_OPEN_POSITIONS_GLOBAL = int(os.getenv("MAX_OPEN_POSITIONS", "30"))
MAX_OPEN_POSITIONS_PER_WALLET = int(os.getenv("MAX_POSITIONS_PER_WALLET", "3"))


@dataclass
class ExposureCheck:
    can_trade: bool
    current_exposure_pct: float
    current_position_count: int
    correlated_count: int
    reject_reason: str | None


async def check_exposure(
    db: AsyncSession,
    market_id,
    proposed_size_usd: float,
    total_bankroll: float,
    wallet_id=None,
    max_total_exposure_pct: float = 0.12,
    max_correlated_positions: int = 1,
    max_position_pct: float = 0.04,
) -> ExposureCheck:
    """Check whether a proposed trade would violate any exposure limits.

    Hard caps:
    - MAX_OPEN_POSITIONS_GLOBAL: system-wide open position ceiling
    - MAX_OPEN_POSITIONS_PER_WALLET: per-wallet open position ceiling
    - max_total_exposure_pct: 12% of bankroll max (was 20%)
    - max_correlated_positions: 1 per market (was 2)
    - max_position_pct: 4% per trade (was 8%)
    """
    open_positions = await db.execute(
        select(PaperPosition).where(PaperPosition.status == "open")
    )
    positions = open_positions.scalars().all()

    # Hard cap: global open position ceiling
    if len(positions) >= MAX_OPEN_POSITIONS_GLOBAL:
        return ExposureCheck(
            can_trade=False,
            current_exposure_pct=0.0,
            current_position_count=len(positions),
            correlated_count=0,
            reject_reason=f"global_position_cap: {len(positions)} >= {MAX_OPEN_POSITIONS_GLOBAL}",
        )

    total_exposure = sum(float(p.total_cost or 0) for p in positions)
    current_exposure_pct = total_exposure / max(total_bankroll, 1)

    # Hard gate: if already over exposure limit, reject immediately
    if current_exposure_pct >= max_total_exposure_pct:
        return ExposureCheck(
            can_trade=False,
            current_exposure_pct=current_exposure_pct,
            current_position_count=len(positions),
            correlated_count=0,
            reject_reason=f"exposure_over_limit: {current_exposure_pct:.1%} >= {max_total_exposure_pct:.0%}",
        )

    # Per-wallet cap
    if wallet_id is not None:
        wallet_open = [p for p in positions if str(p.source_wallet_id) == str(wallet_id)]
        if len(wallet_open) >= MAX_OPEN_POSITIONS_PER_WALLET:
            return ExposureCheck(
                can_trade=False,
                current_exposure_pct=current_exposure_pct,
                current_position_count=len(positions),
                correlated_count=0,
                reject_reason=f"wallet_position_cap: {len(wallet_open)} >= {MAX_OPEN_POSITIONS_PER_WALLET}",
            )

    # Correlated positions in same market
    same_market = [p for p in positions if str(p.market_id) == str(market_id)]
    correlated_count = len(same_market)

    new_exposure_pct = (total_exposure + proposed_size_usd) / max(total_bankroll, 1)

    if new_exposure_pct > max_total_exposure_pct:
        return ExposureCheck(
            can_trade=False,
            current_exposure_pct=current_exposure_pct,
            current_position_count=len(positions),
            correlated_count=correlated_count,
            reject_reason=f"max_exposure: {new_exposure_pct:.1%} > {max_total_exposure_pct:.0%}",
        )

    if correlated_count >= max_correlated_positions:
        return ExposureCheck(
            can_trade=False,
            current_exposure_pct=current_exposure_pct,
            current_position_count=len(positions),
            correlated_count=correlated_count,
            reject_reason=f"max_correlated: {correlated_count} >= {max_correlated_positions}",
        )

    position_pct = proposed_size_usd / max(total_bankroll, 1)
    if position_pct > max_position_pct:
        return ExposureCheck(
            can_trade=False,
            current_exposure_pct=current_exposure_pct,
            current_position_count=len(positions),
            correlated_count=correlated_count,
            reject_reason=f"max_position: {position_pct:.1%} > {max_position_pct:.0%}",
        )

    return ExposureCheck(
        can_trade=True,
        current_exposure_pct=current_exposure_pct,
        current_position_count=len(positions),
        correlated_count=correlated_count,
        reject_reason=None,
    )


async def get_current_bankroll(db: AsyncSession, starting_balance: float = 5000.0) -> float:
    """Get current cash balance from latest portfolio snapshot."""
    result = await db.execute(
        select(PortfolioSnapshot).order_by(PortfolioSnapshot.captured_at.desc()).limit(1)
    )
    snap = result.scalar_one_or_none()
    return float(snap.cash_balance) if snap and snap.cash_balance else starting_balance
