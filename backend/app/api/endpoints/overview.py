from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models.paper import PaperPosition
from app.models.portfolio import PortfolioSnapshot
from app.models.signal import TradeSignal

router = APIRouter()


@router.get("/overview")
async def get_overview(db: AsyncSession = Depends(get_db)) -> dict:
    latest_snapshot = await db.execute(
        select(PortfolioSnapshot).order_by(PortfolioSnapshot.captured_at.desc()).limit(1)
    )
    snap = latest_snapshot.scalar_one_or_none()

    open_count = await db.execute(
        select(func.count()).where(PaperPosition.status == "open")
    )
    signal_count = await db.execute(
        select(func.count()).select_from(TradeSignal)
    )

    return {
        "paper_balance": float(snap.cash_balance) if snap else 5000.0,
        "total_equity": float(snap.total_equity) if snap else 5000.0,
        "unrealized_pnl": float(snap.unrealized_pnl) if snap else 0.0,
        "realized_pnl": float(snap.realized_pnl_cumulative) if snap else 0.0,
        "open_positions": open_count.scalar() or 0,
        "total_signals": signal_count.scalar() or 0,
        "max_drawdown": float(snap.max_drawdown) if snap and snap.max_drawdown else 0.0,
        "demo_mode": True,
    }
