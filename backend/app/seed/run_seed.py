"""Main seed runner: populates all demo data in correct order."""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.dependencies import async_session_factory, engine
from app.models import Base
from app.models.profile import LatencyProfile, RiskProfile, SlippageProfile
from app.seed.seed_wallets import seed_wallets
from app.seed.seed_markets import seed_markets
from app.seed.seed_relationships import seed_relationships
from app.seed.seed_events import seed_events


async def run_seed() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_factory() as db:
        # Skip if already seeded (idempotent)
        result = await db.execute(text("SELECT COUNT(*) FROM wallets"))
        wallet_count = result.scalar()
        if wallet_count and wallet_count > 0:
            print(f"Seed already present ({wallet_count} wallets). Skipping.")
            return

        # Default profiles
        db.add(LatencyProfile(name="realistic", detection_delay_ms=500, decision_delay_ms=200, execution_delay_ms=300, is_default=True))
        db.add(LatencyProfile(name="optimistic", detection_delay_ms=200, decision_delay_ms=100, execution_delay_ms=150, is_default=False))
        db.add(LatencyProfile(name="pessimistic", detection_delay_ms=1000, decision_delay_ms=400, execution_delay_ms=600, is_default=False))

        db.add(SlippageProfile(name="realistic", base_slippage_bps=20, volatility_multiplier=1.5, depth_factor=0.5, use_book_walking=True, is_default=True))
        db.add(SlippageProfile(name="optimistic", base_slippage_bps=10, volatility_multiplier=1.0, depth_factor=0.3, use_book_walking=True, is_default=False))
        db.add(SlippageProfile(name="pessimistic", base_slippage_bps=40, volatility_multiplier=2.5, depth_factor=1.0, use_book_walking=True, is_default=False))

        db.add(RiskProfile(name="default", starting_balance=900.0, is_default=True))
        db.add(RiskProfile(name="conservative", starting_balance=900.0, max_position_pct=0.05, max_total_exposure_pct=0.12, kelly_fraction=0.15, is_default=False))
        db.add(RiskProfile(name="aggressive", starting_balance=900.0, max_position_pct=0.12, max_total_exposure_pct=0.30, kelly_fraction=0.40, is_default=False))

        await db.flush()

        wallets = await seed_wallets(db)
        markets = await seed_markets(db)
        await seed_relationships(db)
        event_count = await seed_events(db, wallets, markets)

        await db.commit()
        print(f"Seeded: {len(wallets)} wallets, {len(markets)} markets, {event_count} events")


if __name__ == "__main__":
    asyncio.run(run_seed())
