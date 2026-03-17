import asyncio, time
import sys
sys.path.insert(0, '/app')

async def main():
    from app.dependencies import async_session_factory
    from app.intelligence.wallet_tracker import get_tracked_wallets, detect_new_trades_batch
    from app.risk.kill_switch import check_kill_switch
    from app.risk.exposure_manager import get_current_bankroll
    from app.models.market import Market, MarketSnapshot
    from app.models.wallet import WalletScore
    from sqlalchemy import select, func
    from datetime import datetime, timezone, timedelta

    async with async_session_factory() as db:
        t = time.time()
        kill = await check_kill_switch(db)
        print(f'kill_switch: {time.time()-t:.3f}s kill_active={kill.is_active}')

        t = time.time()
        wallets = await get_tracked_wallets(db)
        print(f'get_tracked_wallets: {len(wallets)} wallets {time.time()-t:.3f}s')

        t = time.time()
        wallet_ids = [w.id for w in wallets]
        subq = (select(WalletScore.wallet_id, func.max(WalletScore.scored_at).label("latest")).group_by(WalletScore.wallet_id).subquery())
        score_rows = await db.execute(select(WalletScore).join(subq, (WalletScore.wallet_id == subq.c.wallet_id) & (WalletScore.scored_at == subq.c.latest)).where(WalletScore.wallet_id.in_(wallet_ids)))
        scores = score_rows.scalars().all()
        print(f'batch wallet scores: {len(scores)} in {time.time()-t:.3f}s')

        t = time.time()
        total_bankroll = await get_current_bankroll(db)
        print(f'bankroll: {total_bankroll:.2f} in {time.time()-t:.3f}s')

        t = time.time()
        since = datetime.now(timezone.utc) - timedelta(minutes=15)
        trades_map = await detect_new_trades_batch(db, wallet_ids, since=since, limit_per_wallet=1)
        wallets_with_trades = sum(1 for v in trades_map.values() if v)
        print(f'detect_new_trades_batch: {wallets_with_trades} wallets with trades in {time.time()-t:.3f}s')

        t = time.time()
        market_ids = list({tx.market_id for trades in trades_map.values() for tx in trades})
        print(f'unique markets to snapshot: {len(market_ids)}')
        snap_subq = (select(MarketSnapshot.market_id, func.max(MarketSnapshot.captured_at).label("latest")).group_by(MarketSnapshot.market_id).where(MarketSnapshot.market_id.in_(market_ids)).subquery())
        snap_rows = await db.execute(select(MarketSnapshot).join(snap_subq, (MarketSnapshot.market_id == snap_subq.c.market_id) & (MarketSnapshot.captured_at == snap_subq.c.latest)))
        snaps = {s.market_id: s for s in snap_rows.scalars().all()}
        print(f'batch snapshots: {len(snaps)} in {time.time()-t:.3f}s')

asyncio.run(main())
