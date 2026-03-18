from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models.paper import PaperFill, PaperOrder, PaperPosition
from app.models.market import Market, MarketSnapshot

router = APIRouter()


def _serialize_position(p: PaperPosition, market_question: str | None = None) -> dict:
    return {
        "id": str(p.id),
        "market_id": str(p.market_id),
        "market_question": market_question,
        "strategy": p.strategy,
        "side": p.side,
        "outcome": p.outcome,
        "avg_entry_price": float(p.avg_entry_price) if p.avg_entry_price else None,
        "avg_exit_price": float(p.avg_exit_price) if p.avg_exit_price else None,
        "total_size": float(p.total_size) if p.total_size else None,
        "realized_pnl": float(p.realized_pnl) if p.realized_pnl is not None else None,
        "unrealized_pnl": float(p.unrealized_pnl) if p.unrealized_pnl is not None else None,
        "total_fees": float(p.total_fees) if p.total_fees else None,
        "total_slippage": float(p.total_slippage) if p.total_slippage else None,
        "status": p.status,
        "exit_reason": p.exit_reason,
        "epoch": p.epoch,
        "conviction_tier": p.conviction_tier,
        "opened_at": p.opened_at.isoformat() if p.opened_at else None,
        "closed_at": p.closed_at.isoformat() if p.closed_at else None,
        "target_structure": p.target_structure,
        "position_group_id": str(p.position_group_id) if p.position_group_id else None,
        "leg_index": p.leg_index,
    }


@router.get("")
async def list_trades(
    status: str | None = Query(None, description="open | closed"),
    strategy: str | None = Query(None),
    exit_reason: str | None = Query(None),
    hide_zero_pnl: bool = Query(False, description="Exclude closed positions with PnL=0"),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict:
    base = select(PaperPosition)

    if status:
        base = base.where(PaperPosition.status == status)
    if strategy:
        base = base.where(PaperPosition.strategy == strategy)
    if exit_reason:
        base = base.where(PaperPosition.exit_reason == exit_reason)
    if hide_zero_pnl:
        base = base.where(
            (PaperPosition.status == "open") |
            (
                (PaperPosition.status == "closed") &
                (PaperPosition.realized_pnl != 0)
            )
        )

    # Total count for pagination (filtered)
    count_q = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    # Global open/closed counts (unfiltered — for summary bar)
    total_open_q   = select(func.count()).where(PaperPosition.status == "open")
    total_closed_q = select(func.count()).where(PaperPosition.status == "closed")
    total_open   = (await db.execute(total_open_q)).scalar()   or 0
    total_closed = (await db.execute(total_closed_q)).scalar() or 0

    # Last position opened at (for "no new trades" diagnostic)
    last_opened_q = select(func.max(PaperPosition.opened_at)).select_from(PaperPosition)
    last_opened = (await db.execute(last_opened_q)).scalar()
    last_position_opened_at = last_opened.isoformat() if last_opened else None

    # Fetch page
    q = base.order_by(PaperPosition.opened_at.desc()).limit(limit).offset(offset)
    result = await db.execute(q)
    positions = result.scalars().all()

    # Batch-fetch market questions
    market_ids = list({p.market_id for p in positions if p.market_id})
    mkt_map: dict = {}
    if market_ids:
        mkts = await db.execute(select(Market).where(Market.id.in_(market_ids)))
        for m in mkts.scalars().all():
            mkt_map[m.id] = m.question

    # Live PnL: compute unrealized_pnl from latest snapshot for open positions
    open_market_ids = [p.market_id for p in positions if p.status == "open"]
    live_prices: dict = {}
    if open_market_ids:
        snap_subq = (
            select(MarketSnapshot.market_id, func.max(MarketSnapshot.captured_at).label("latest"))
            .where(MarketSnapshot.market_id.in_(open_market_ids))
            .group_by(MarketSnapshot.market_id)
            .subquery()
        )
        snap_rows = await db.execute(
            select(MarketSnapshot)
            .join(snap_subq, (MarketSnapshot.market_id == snap_subq.c.market_id) & (MarketSnapshot.captured_at == snap_subq.c.latest))
        )
        for s in snap_rows.scalars().all():
            live_prices[s.market_id] = float(s.midpoint or s.last_trade_price or 0)

    for p in positions:
        if p.status == "open" and p.market_id in live_prices:
            current = live_prices[p.market_id]
            entry = float(p.avg_entry_price or 0)
            size = float(p.total_size or 0)
            if p.side == "BUY":
                p.unrealized_pnl = (current - entry) * size
            else:
                p.unrealized_pnl = (entry - current) * size

    # Distinct strategy values for filter dropdown
    strat_q = await db.execute(
        select(PaperPosition.strategy).distinct().order_by(PaperPosition.strategy)
    )
    strategies = [r[0] for r in strat_q.all() if r[0]]

    exit_q = await db.execute(
        select(PaperPosition.exit_reason).distinct().order_by(PaperPosition.exit_reason)
    )
    exit_reasons = [r[0] for r in exit_q.all() if r[0]]

    return {
        "total": total,
        "total_open": total_open,
        "total_closed": total_closed,
        "last_position_opened_at": last_position_opened_at,
        "limit": limit,
        "offset": offset,
        "strategies": strategies,
        "exit_reasons": exit_reasons,
        "trades": [_serialize_position(p, mkt_map.get(p.market_id)) for p in positions],
    }


@router.get("/{trade_id}")
async def get_trade_detail(trade_id: UUID, db: AsyncSession = Depends(get_db)) -> dict:
    position = await db.get(PaperPosition, trade_id)
    if not position:
        return {"error": "not found"}

    orders_result = await db.execute(
        select(PaperOrder)
        .where(PaperOrder.signal_id == position.strategy)
        .order_by(PaperOrder.created_at)
    )

    fills_q = (
        select(PaperFill)
        .join(PaperOrder, PaperOrder.id == PaperFill.order_id)
        .where(PaperOrder.market_id == position.market_id)
        .order_by(PaperFill.filled_at)
    )
    fills_result = await db.execute(fills_q)
    fills = fills_result.scalars().all()

    return {
        "position": {
            "id": str(position.id),
            "market_id": str(position.market_id),
            "strategy": position.strategy,
            "status": position.status,
            "target_structure": position.target_structure,
        },
        "fills": [
            {
                "fill_price": float(f.fill_price) if f.fill_price else None,
                "fill_size": float(f.fill_size) if f.fill_size else None,
                "slippage": float(f.slippage) if f.slippage else None,
                "fee": float(f.fee) if f.fee else None,
                "levels_consumed": f.levels_consumed,
                "wavg_fill_price": float(f.wavg_fill_price) if f.wavg_fill_price else None,
            }
            for f in fills
        ],
    }
