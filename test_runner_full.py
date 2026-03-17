import asyncio, time
import sys
sys.path.insert(0, '/app')

async def main():
    from app.strategies.runner import StrategyRunner
    from app.dependencies import async_session_factory
    runner = StrategyRunner()
    t = time.time()
    async with async_session_factory() as db:
        stats = await runner.run_cycle(db)
        await db.commit()
    elapsed = time.time() - t
    print(f'elapsed={elapsed:.1f}s signals={stats.get("signals_generated")} trades={stats.get("trades_executed")}')
    funnel = stats.get('strategy_funnel', {})
    for s, f in funnel.items():
        print(f'  {s}: signals={f.get("signals_generated",0)} exec={f.get("trades_executed",0)}')

asyncio.run(main())
