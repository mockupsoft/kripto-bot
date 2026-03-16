"""Run the demo simulation: processes seeded events through the full pipeline."""

import asyncio
import json

from app.dependencies import async_session_factory
from app.strategies.runner import StrategyRunner


async def main():
    runner = StrategyRunner()

    for cycle in range(3):
        async with async_session_factory() as db:
            stats = await runner.run_cycle(db)
            await db.commit()
            print(f"Cycle {cycle + 1}: {json.dumps(stats, indent=2)}")


if __name__ == "__main__":
    asyncio.run(main())
