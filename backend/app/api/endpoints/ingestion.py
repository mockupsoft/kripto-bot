"""Live ingestion control endpoints."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.ingestion.live_ingestion import run_live_ingestion_cycle, ingest_wallet_activity

logger = logging.getLogger(__name__)
router = APIRouter()

_polling_task: asyncio.Task | None = None


@router.post("/trigger")
async def trigger_ingestion() -> dict:
    """Manually trigger one live ingestion cycle."""
    result = await run_live_ingestion_cycle()
    return {"status": "completed", **result}


@router.post("/add-wallet")
async def add_wallet_for_tracking(address: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Add a real Polymarket wallet address to track."""
    from sqlalchemy import select
    from app.models.wallet import Wallet

    existing = await db.execute(select(Wallet).where(Wallet.address == address))
    wallet = existing.scalar_one_or_none()

    if wallet:
        wallet.is_tracked = True
        await db.commit()
        return {"status": "already_exists", "wallet_id": str(wallet.id), "tracking": True}

    wallet = Wallet(
        address=address,
        label=f"{address[:10]}...{address[-6:]}",
        is_tracked=True,
        metadata={"source": "manual_add", "real_wallet": True},
    )
    db.add(wallet)
    await db.commit()

    # Immediately fetch their recent activity
    count = await ingest_wallet_activity(address)

    return {
        "status": "added",
        "wallet_id": str(wallet.id),
        "tracking": True,
        "trades_ingested": count,
    }


@router.post("/run-exit-engine")
async def run_exit_engine_now(db: AsyncSession = Depends(get_db)) -> dict:
    """Manually run the exit engine to close positions that hit exit rules."""
    from app.execution.exit_engine import run_exit_cycle
    result = await run_exit_cycle(db)
    return {"status": "completed", **result}


async def ingestion_status() -> dict:
    """Get current ingestion status."""
    from sqlalchemy import select, func
    from app.dependencies import async_session_factory
    from app.models.event import RawEvent
    from app.models.market import Market
    from app.models.wallet import Wallet

    async with async_session_factory() as db:
        total_events = await db.scalar(select(func.count(RawEvent.id)))
        unprocessed = await db.scalar(select(func.count(RawEvent.id)).where(RawEvent.processed == False))  # noqa
        live_markets = await db.scalar(select(func.count(Market.id)).where(Market.is_active == True))  # noqa
        tracked_wallets = await db.scalar(select(func.count(Wallet.id)).where(Wallet.is_tracked == True))  # noqa
        real_wallets = await db.scalar(
            select(func.count(Wallet.id)).where(
                Wallet.is_tracked == True,  # noqa
                ~Wallet.address.like("0xdemo%"),
            )
        )

    return {
        "total_raw_events": total_events,
        "unprocessed_events": unprocessed,
        "live_markets_in_db": live_markets,
        "tracked_wallets": tracked_wallets,
        "real_wallets": real_wallets,
        "demo_wallets": (tracked_wallets or 0) - (real_wallets or 0),
    }
