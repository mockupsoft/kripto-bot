import asyncio, time
import sys
sys.path.insert(0, '/app')

async def main():
    from app.dependencies import async_session_factory
    from app.intelligence.wallet_tracker import get_tracked_wallets, detect_new_trades_batch
    from app.models.market import Market, MarketSnapshot
    from app.models.wallet import WalletScore
    from app.signals.signal_generator import SignalGenerator
    from sqlalchemy import select, func
    from datetime import datetime, timezone, timedelta

    async with async_session_factory() as db:
        wallets = await get_tracked_wallets(db)
        wallet_ids = [w.id for w in wallets]
        since = datetime.now(timezone.utc) - timedelta(minutes=15)

        trades_map = await detect_new_trades_batch(db, wallet_ids, since=since, limit_per_wallet=1)
        wallets_with_trades = [(w, trades_map[w.id]) for w in wallets if w.id in trades_map and trades_map[w.id]]

        all_market_ids = list({tx.market_id for _, trades in wallets_with_trades for tx in trades})
        snap_subq = (select(MarketSnapshot.market_id, func.max(MarketSnapshot.captured_at).label("latest")).group_by(MarketSnapshot.market_id).where(MarketSnapshot.market_id.in_(all_market_ids)).subquery())
        snap_rows = await db.execute(select(MarketSnapshot).join(snap_subq, (MarketSnapshot.market_id == snap_subq.c.market_id) & (MarketSnapshot.captured_at == snap_subq.c.latest)))
        snap_cache = {s.market_id: s for s in snap_rows.scalars().all()}
        mkt_rows = await db.execute(select(Market).where(Market.id.in_(all_market_ids)))
        mkt_cache = {m.id: m for m in mkt_rows.scalars().all()}

        sg = SignalGenerator()
        t = time.time()
        sig_count = 0
        for w, trades in wallets_with_trades[:50]:  # First 50 wallets
            for tx in trades:
                snap = snap_cache.get(tx.market_id)
                market = mkt_cache.get(tx.market_id)
                if not snap or not market:
                    continue
                price = float(snap.midpoint or snap.best_bid or 0.5)
                spread = float(snap.spread or 0.04)
                sig = await sg.generate_copy_signal(
                    db=db, strategy='direct_copy', wallet_id=w.id,
                    market_id=tx.market_id, market_price=price,
                    fees_enabled=market.fees_enabled, fee_rate_bps=market.fee_rate_bps,
                    spread=spread, wallet_score=0.5,
                )
                sig_count += 1
        elapsed = time.time() - t
        print(f'50 wallets, {sig_count} signals in {elapsed:.2f}s ({elapsed/max(1,sig_count)*1000:.1f}ms/sig)')
        print(f'Projected for 196 wallets x 4 strategies: {elapsed/50*196*4:.1f}s')

asyncio.run(main())
