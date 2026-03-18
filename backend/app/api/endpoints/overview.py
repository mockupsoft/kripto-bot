from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.dependencies import get_db
from app.models.paper import PaperPosition
from app.models.portfolio import PortfolioSnapshot
from app.models.signal import TradeSignal

router = APIRouter()


@router.get("/overview")
async def get_overview(db: AsyncSession = Depends(get_db)) -> dict:
    settings = get_settings()
    current_epoch = settings.TRADE_EPOCH

    # Realized PnL: only current epoch
    realized_q = await db.execute(
        select(func.coalesce(func.sum(PaperPosition.realized_pnl), 0))
        .where(PaperPosition.status == "closed", PaperPosition.epoch == current_epoch)
    )
    realized_pnl = float(realized_q.scalar() or 0)

    # Unrealized PnL: only current epoch open positions
    unrealized_q = await db.execute(
        select(func.coalesce(func.sum(PaperPosition.unrealized_pnl), 0))
        .where(PaperPosition.status == "open", PaperPosition.epoch == current_epoch)
    )
    unrealized_pnl = float(unrealized_q.scalar() or 0)

    # Open position count: current epoch only
    open_count = await db.execute(
        select(func.count())
        .where(PaperPosition.status == "open", PaperPosition.epoch == current_epoch)
    )

    # Deployed capital in current epoch
    deployed_q = await db.execute(
        select(func.coalesce(func.sum(PaperPosition.total_cost), 0))
        .where(PaperPosition.status == "open", PaperPosition.epoch == current_epoch)
    )
    deployed = float(deployed_q.scalar() or 0)

    starting = settings.STARTING_BALANCE
    cash = starting + realized_pnl - deployed
    equity = cash + deployed + unrealized_pnl

    signal_count = await db.execute(
        select(func.count()).select_from(TradeSignal)
    )

    # Max drawdown from portfolio snapshot (global)
    latest_snapshot = await db.execute(
        select(PortfolioSnapshot).order_by(PortfolioSnapshot.captured_at.desc()).limit(1)
    )
    snap = latest_snapshot.scalar_one_or_none()

    return {
        "paper_balance": round(cash, 4),
        "total_equity": round(equity, 4),
        "unrealized_pnl": round(unrealized_pnl, 4),
        "realized_pnl": round(realized_pnl, 4),
        "open_positions": open_count.scalar() or 0,
        "total_signals": signal_count.scalar() or 0,
        "max_drawdown": float(snap.max_drawdown) if snap and snap.max_drawdown else 0.0,
        "demo_mode": True,
        "epoch": current_epoch,
    }
