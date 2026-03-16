from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models.profile import LatencyProfile, RiskProfile, SlippageProfile

router = APIRouter()


@router.get("")
async def get_settings(db: AsyncSession = Depends(get_db)) -> dict:
    latency = await db.execute(select(LatencyProfile).where(LatencyProfile.is_default.is_(True)).limit(1))
    slippage = await db.execute(select(SlippageProfile).where(SlippageProfile.is_default.is_(True)).limit(1))
    risk = await db.execute(select(RiskProfile).where(RiskProfile.is_default.is_(True)).limit(1))

    lp = latency.scalar_one_or_none()
    sp = slippage.scalar_one_or_none()
    rp = risk.scalar_one_or_none()

    return {
        "latency": {
            "detection_delay_ms": lp.detection_delay_ms if lp else 500,
            "decision_delay_ms": lp.decision_delay_ms if lp else 200,
            "execution_delay_ms": lp.execution_delay_ms if lp else 300,
        },
        "slippage": {
            "base_slippage_bps": sp.base_slippage_bps if sp else 20,
            "volatility_multiplier": float(sp.volatility_multiplier) if sp else 1.5,
            "use_book_walking": sp.use_book_walking if sp else True,
        },
        "risk": {
            "starting_balance": float(rp.starting_balance) if rp else 900.0,
            "max_position_pct": float(rp.max_position_pct) if rp else 0.08,
            "max_total_exposure_pct": float(rp.max_total_exposure_pct) if rp else 0.20,
            "max_correlated_positions": rp.max_correlated_positions if rp else 2,
            "consecutive_loss_cooldown": rp.consecutive_loss_cooldown if rp else 5,
            "kelly_fraction": float(rp.kelly_fraction) if rp else 0.25,
            "daily_loss_stop_pct": float(rp.daily_loss_stop_pct) if rp else 0.10,
        },
    }
