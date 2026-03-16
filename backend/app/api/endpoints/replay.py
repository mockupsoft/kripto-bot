from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models.strategy import ReplaySession
from app.simulation.replay_engine import start_replay as create_replay, run_replay
from app.simulation.monte_carlo import run_monte_carlo
from app.simulation.sensitivity import latency_sensitivity_sweep, slippage_sensitivity_sweep

router = APIRouter()


class ReplayRequest(BaseModel):
    name: str = "Demo Replay"
    start_time: datetime | None = None
    end_time: datetime | None = None
    speed_multiplier: float = 10.0
    scenario: str = "realistic"
    random_seed: int | None = None
    is_deterministic: bool = True


class MonteCarloRequest(BaseModel):
    n_simulations: int = 600
    n_trades: int = 100
    win_probability: float = 0.55
    avg_win_pct: float = 0.03
    avg_loss_pct: float = 0.025
    random_seed: int | None = None


@router.post("/start")
async def start_replay_endpoint(req: ReplayRequest, db: AsyncSession = Depends(get_db)) -> dict:
    from datetime import timezone, timedelta
    now = datetime.now(timezone.utc)
    session = await create_replay(
        db=db,
        name=req.name,
        start_time=req.start_time or (now - timedelta(hours=48)),
        end_time=req.end_time or now,
        speed_multiplier=req.speed_multiplier,
        scenario=req.scenario,
        random_seed=req.random_seed,
        is_deterministic=req.is_deterministic,
    )
    await db.commit()

    results = await run_replay(db, session.id)
    await db.commit()

    return {
        "id": str(session.id),
        "status": "completed",
        "results": results,
    }


@router.get("/{session_id}/status")
async def get_replay_status(session_id: UUID, db: AsyncSession = Depends(get_db)) -> dict:
    session = await db.get(ReplaySession, session_id)
    if not session:
        return {"error": "not found"}
    return {
        "id": str(session.id),
        "name": session.name,
        "status": session.status,
        "speed": float(session.speed_multiplier),
        "is_deterministic": session.is_deterministic,
        "results": session.results,
    }


@router.post("/monte-carlo")
async def run_monte_carlo_endpoint(req: MonteCarloRequest) -> dict:
    result = run_monte_carlo(
        n_simulations=req.n_simulations,
        n_trades=req.n_trades,
        win_probability=req.win_probability,
        avg_win_pct=req.avg_win_pct,
        avg_loss_pct=req.avg_loss_pct,
        random_seed=req.random_seed,
    )
    return {
        "n_simulations": result.n_simulations,
        "percentiles": result.percentiles,
        "max_drawdown_distribution": result.max_drawdown_distribution,
        "ruin_probability": result.ruin_probability,
        "median_final_equity": result.median_final_equity,
        "mean_final_equity": result.mean_final_equity,
    }


@router.get("/sensitivity/latency")
async def latency_sweep() -> dict:
    results = latency_sensitivity_sweep(n_simulations=200, random_seed=42)
    return {
        "sweep": [
            {
                "delay_ms": int(r.parameter_value),
                "median_equity": r.median_equity,
                "ruin_probability": r.ruin_probability,
                "p5_equity": r.p5_equity,
            }
            for r in results
        ]
    }


@router.get("/sensitivity/slippage")
async def slippage_sweep() -> dict:
    results = slippage_sensitivity_sweep(n_simulations=200, random_seed=42)
    return {
        "sweep": [
            {
                "multiplier": r.parameter_value,
                "median_equity": r.median_equity,
                "ruin_probability": r.ruin_probability,
                "p5_equity": r.p5_equity,
            }
            for r in results
        ]
    }
