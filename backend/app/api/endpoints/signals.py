from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models.signal import SignalDecision, TradeSignal

router = APIRouter()


@router.get("")
async def list_signals(
    strategy: str | None = Query(None),
    source_type: str | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from sqlalchemy import func as sqlfunc

    base_q = select(TradeSignal)
    if strategy:
        base_q = base_q.where(TradeSignal.strategy == strategy)
    if source_type:
        base_q = base_q.where(TradeSignal.source_type == source_type)

    total_result = await db.execute(select(sqlfunc.count()).select_from(base_q.subquery()))
    total = total_result.scalar() or 0

    q = base_q.order_by(TradeSignal.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(q)
    sigs = result.scalars().all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "signals": [
            {
                "id": str(s.id),
                "strategy": s.strategy,
                "source_type": s.source_type,
                "market_id": str(s.market_id) if s.market_id else None,
                "side": s.side,
                "model_probability": float(s.model_probability) if s.model_probability else None,
                "model_confidence": float(s.model_confidence) if s.model_confidence else None,
                "market_price": float(s.market_price) if s.market_price else None,
                "raw_edge": float(s.raw_edge) if s.raw_edge else None,
                "net_edge": float(s.net_edge) if s.net_edge else None,
                "spread_z_score": float(s.spread_z_score) if s.spread_z_score else None,
                "costs_breakdown": s.costs_breakdown,
                "created_at": s.created_at.isoformat(),
            }
            for s in sigs
        ],
    }


@router.get("/{signal_id}/audit")
async def get_signal_audit(signal_id: UUID, db: AsyncSession = Depends(get_db)) -> dict:
    signal = await db.get(TradeSignal, signal_id)
    if not signal:
        return {"error": "not found"}

    decisions_result = await db.execute(
        select(SignalDecision)
        .where(SignalDecision.signal_id == signal_id)
        .order_by(SignalDecision.decided_at)
    )
    decisions = decisions_result.scalars().all()

    return {
        "signal": {
            "id": str(signal.id),
            "strategy": signal.strategy,
            "source_type": signal.source_type,
            "side": signal.side,
            "model_probability": float(signal.model_probability) if signal.model_probability else None,
            "model_confidence": float(signal.model_confidence) if signal.model_confidence else None,
            "market_price": float(signal.market_price) if signal.market_price else None,
            "raw_edge": float(signal.raw_edge) if signal.raw_edge else None,
            "net_edge": float(signal.net_edge) if signal.net_edge else None,
            "costs_breakdown": signal.costs_breakdown,
        },
        "decisions": [
            {
                "id": str(d.id),
                "decision": d.decision,
                "reject_reason": d.reject_reason,
                "signal_age_ms": d.signal_age_ms,
                "detection_to_decision_ms": d.detection_to_decision_ms,
                "price_at_decision": float(d.price_at_decision) if d.price_at_decision else None,
                "price_drift": float(d.price_drift) if d.price_drift else None,
                "spread_at_decision": float(d.spread_at_decision) if d.spread_at_decision else None,
                "kelly_fraction": float(d.kelly_fraction) if d.kelly_fraction else None,
                "proposed_size": float(d.proposed_size) if d.proposed_size else None,
                "available_bankroll": float(d.available_bankroll) if d.available_bankroll else None,
                "current_exposure_pct": float(d.current_exposure_pct) if d.current_exposure_pct else None,
                "edge_at_signal": float(d.edge_at_signal) if d.edge_at_signal else None,
                "edge_at_decision": float(d.edge_at_decision) if d.edge_at_decision else None,
                "edge_erosion_pct": float(d.edge_erosion_pct) if d.edge_erosion_pct else None,
                "scenario": d.scenario,
                "decided_at": d.decided_at.isoformat(),
            }
            for d in decisions
        ],
    }
