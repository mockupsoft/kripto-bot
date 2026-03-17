import asyncio, time
import sys
sys.path.insert(0, '/app')

async def main():
    from app.dependencies import async_session_factory
    from app.intelligence.wallet_tracker import get_tracked_wallets, detect_new_trades_batch
    from datetime import datetime, timezone, timedelta

    async with async_session_factory() as db:
        t0 = time.time()
        wallets = await get_tracked_wallets(db)
        t1 = time.time()
        print(f'get_tracked_wallets: {len(wallets)} wallets in {t1-t0:.2f}s')

        since = datetime.now(timezone.utc) - timedelta(minutes=15)
        trades_map = await detect_new_trades_batch(db, [w.id for w in wallets], since=since, limit_per_wallet=1)
        t2 = time.time()
        wallets_with_trades = sum(1 for v in trades_map.values() if v)
        total_trades = sum(len(v) for v in trades_map.values())
        print(f'detect_new_trades_batch: {wallets_with_trades} wallets with trades, {total_trades} total in {t2-t1:.2f}s')

asyncio.run(main())
