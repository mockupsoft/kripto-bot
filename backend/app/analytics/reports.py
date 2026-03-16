"""Report generation: daily, per-wallet, per-strategy."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.metrics import compute_metrics
from app.models.paper import PaperPosition


async def generate_strategy_report(db: AsyncSession, strategy: str | None = None) -> dict:
    q = select(PaperPosition).where(PaperPosition.status == "closed")
    if strategy:
        q = q.where(PaperPosition.strategy == strategy)

    result = await db.execute(q)
    positions = result.scalars().all()

    pnls = [float(p.realized_pnl) for p in positions if p.realized_pnl]
    fees = [float(p.total_fees or 0) for p in positions]
    slippages = [float(p.total_slippage or 0) for p in positions]

    metrics = compute_metrics(pnls, fees, slippages)

    return {
        "strategy": strategy or "all",
        "total_trades": metrics.total_trades,
        "win_rate": metrics.win_rate,
        "gross_pnl": metrics.gross_pnl,
        "net_pnl": metrics.net_pnl,
        "profit_factor": metrics.profit_factor,
        "sharpe_ratio": metrics.sharpe_ratio,
        "max_drawdown": metrics.max_drawdown,
    }
