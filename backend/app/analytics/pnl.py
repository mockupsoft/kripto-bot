"""PnL computation: real-time and historical."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.paper import PaperPosition
from app.models.portfolio import PortfolioSnapshot


async def compute_portfolio_snapshot(
    db: AsyncSession,
    starting_balance: float = 900.0,
    run_id=None,
    config_hash: str | None = None,
) -> PortfolioSnapshot:
    """Compute current portfolio state and persist a snapshot."""
    # Open positions
    open_result = await db.execute(
        select(PaperPosition).where(PaperPosition.status == "open")
    )
    open_positions = open_result.scalars().all()

    # All closed positions for cumulative PnL
    closed_result = await db.execute(
        select(PaperPosition).where(PaperPosition.status == "closed")
    )
    closed_positions = closed_result.scalars().all()

    realized_pnl = sum(float(p.realized_pnl or 0) for p in closed_positions)
    total_fees = sum(float(p.total_fees or 0) for p in closed_positions)
    total_slippage = sum(float(p.total_slippage or 0) for p in closed_positions)

    position_value = sum(float(p.total_cost or 0) for p in open_positions)
    unrealized = sum(float(p.unrealized_pnl or 0) for p in open_positions)

    cash = starting_balance + realized_pnl - position_value
    total_equity = cash + position_value + unrealized

    # Max drawdown from historical snapshots
    prev_result = await db.execute(
        select(PortfolioSnapshot).order_by(PortfolioSnapshot.captured_at.desc()).limit(100)
    )
    prev_snaps = prev_result.scalars().all()
    peak = max((float(s.total_equity) for s in prev_snaps), default=starting_balance)
    peak = max(peak, total_equity)
    drawdown = (peak - total_equity) / peak if peak > 0 else 0

    snap = PortfolioSnapshot(
        captured_at=datetime.now(timezone.utc),
        cash_balance=cash,
        position_value=position_value,
        total_equity=total_equity,
        unrealized_pnl=unrealized,
        realized_pnl_cumulative=realized_pnl,
        open_position_count=len(open_positions),
        max_drawdown=drawdown,
        scenario="realistic",
        run_id=run_id,
        config_hash=config_hash,
    )
    db.add(snap)
    await db.flush()
    return snap
