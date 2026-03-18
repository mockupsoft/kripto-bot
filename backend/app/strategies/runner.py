"""Strategy runner: processes seeded events through the full signal-to-trade pipeline.

This is the main orchestrator that connects:
event normalizer -> wallet tracker -> signal generator -> signal filter ->
strategy evaluation -> paper execution -> portfolio snapshot.

Strategy allocation is governed by StrategyManager:
  - direct_copy / high_conviction: active, capital-weighted by rolling PF
  - dislocation: starts in shadow; auto-activates if rolling PF >= 1.2
  - shadow: always shadow (logging only)

Kill-switch: if last 20 trades PF < 0.8 → strategy enters shadow mode.
"""

from __future__ import annotations

import logging
import os
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.pnl import compute_portfolio_snapshot
from app.intelligence.wallet_tracker import get_tracked_wallets
from app.models.market import Market, MarketRelationship, MarketSnapshot
from app.models.paper import PaperPosition
from app.models.signal import TradeSignal
from app.models.wallet import WalletScore
from app.risk.exposure_manager import check_exposure, get_current_bankroll
from app.risk.kill_switch import check_kill_switch
from app.signals.signal_generator import SignalGenerator

logger = logging.getLogger(__name__)
from app.strategies.direct_copy import DirectCopyStrategy
from app.strategies.dislocation import DislocationStrategy
from app.strategies.high_conviction import HighConvictionCopyStrategy
from app.strategies.leader_copy import LeaderCopyStrategy
from app.strategies.shadow import ShadowModeStrategy
from app.strategies.strategy_manager import get_strategy_bankroll, is_strategy_active


class StrategyRunner:
    def __init__(self):
        self.signal_gen = SignalGenerator()
        self.strategies = {
            "direct_copy": DirectCopyStrategy(),
            "high_conviction": HighConvictionCopyStrategy(),
            "leader_copy": LeaderCopyStrategy(),
            "dislocation": DislocationStrategy(),
            "shadow": ShadowModeStrategy(),
        }
        # Track last run time to bound trade window (avoid reprocessing old trades)
        # Start with recent timestamp so first cycle only processes very fresh trades
        self._last_run_at: datetime = datetime.now(timezone.utc) - timedelta(seconds=60)

    async def run_cycle(self, db: AsyncSession) -> dict:
        """Execute one full processing cycle."""
        tracked_strategies = ["direct_copy", "high_conviction", "leader_copy", "dislocation", "shadow"]
        stats = {
            "events_processed": 0,
            "signals_generated": 0,
            "decisions_made": 0,
            "trades_executed": 0,
            "strategy_modes": {},
            "strategy_funnel": {
                name: {
                    "signals_generated": 0,
                    "decisions_made": 0,
                    "signals_passed_filter": 0,
                    "trades_executed": 0,
                }
                for name in tracked_strategies
            },
        }

        # Event normalization handled by ingestion loop — not runner's responsibility

        # 2. Check global kill switch (skip in research mode)
        _research_mode = os.getenv("STRATEGY_RESEARCH_MODE", "").lower() in ("true", "1", "yes")
        if not _research_mode:
            kill = await check_kill_switch(db)
            if kill.is_active:
                stats["kill_switch"] = kill.reason
                return stats

        # 3. Load tracked wallets (scoring is done externally by live_ingestion)
        wallets = await get_tracked_wallets(db)

        # 3b. Batch preload latest wallet scores for copy strategies (cache-first lookup)
        wallet_ids = [w.id for w in wallets]
        wallet_score_cache: dict[str, tuple[float, float]] = {}
        if wallet_ids:
            subq = (
                select(WalletScore.wallet_id, func.max(WalletScore.scored_at).label("latest"))
                .group_by(WalletScore.wallet_id)
                .subquery()
            )
            score_rows = await db.execute(
                select(WalletScore)
                .join(subq, (WalletScore.wallet_id == subq.c.wallet_id) & (WalletScore.scored_at == subq.c.latest))
                .where(WalletScore.wallet_id.in_(wallet_ids))
            )
            for ws in score_rows.scalars().all():
                composite = float(ws.composite_score or 0) if ws.composite_score is not None else 0.0
                copyability = float(ws.copyability_score or 0) if ws.copyability_score is not None else 0.0
                wallet_score_cache[str(ws.wallet_id)] = (composite, copyability)

        # 4. Total bankroll (used for capital weighting)
        total_bankroll = await get_current_bankroll(db)

        # 4a. Resolve per-strategy bankrolls (respects kill-switch + PF-based weighting)
        copy_strategies = ["direct_copy", "high_conviction", "leader_copy", "shadow"]
        strategy_bankrolls: dict[str, float] = {}
        for sname in [*copy_strategies, "dislocation"]:
            strategy_bankrolls[sname] = await get_strategy_bankroll(db, sname, total_bankroll)

        # Collect mode summary for stats
        for sname, brl in strategy_bankrolls.items():
            is_active = brl > 0
            stats["strategy_modes"][sname] = "active" if is_active else "shadow/paused"

        # 5. Batch-fetch new trades for all eligible wallets in one query
        now_utc = datetime.now(timezone.utc)
        since_cutoff = self._last_run_at - timedelta(seconds=10)
        self._last_run_at = now_utc
        _MIN_RUNNER_COMPOSITE = float(os.getenv("RUNNER_MIN_COMPOSITE", "0.35"))

        # Filter wallets by gate
        eligible_wallets = []
        for w in wallets:
            w_composite_cached = wallet_score_cache.get(str(w.id), (0.0, 0.0))[0]
            if w_composite_cached > 0 and w_composite_cached < _MIN_RUNNER_COMPOSITE:
                continue
            eligible_wallets.append(w)

        # Pre-fetch global exposure state once (avoid per-signal DB hit)
        open_pos_result = await db.execute(
            select(PaperPosition).where(PaperPosition.status == "open")
        )
        open_positions_cache = list(open_pos_result.scalars().all())
        global_pos_count = len(open_positions_cache)
        global_exposure_usd = sum(float(p.total_cost or 0) for p in open_positions_cache)
        global_exposure_pct = global_exposure_usd / max(total_bankroll, 1)
        _MAX_GLOBAL = int(os.getenv("MAX_OPEN_POSITIONS_GLOBAL", "20"))

        # Build position counts per market (for concentration gate in signal_filter)
        position_counts_preloaded: dict = {}
        for p in open_positions_cache:
            position_counts_preloaded[p.market_id] = position_counts_preloaded.get(p.market_id, 0) + 1
        # Track markets that opened a position this cycle to prevent
        # cross-strategy duplicates (direct_copy+high_conviction on same market)
        cycle_opened_markets: set = set()

        # Single batch query for all new trades
        from app.intelligence.wallet_tracker import detect_new_trades_batch
        wallet_trades_map = await detect_new_trades_batch(
            db,
            wallet_ids=[w.id for w in eligible_wallets],
            since=since_cutoff,
            limit_per_wallet=1,  # 1 trade per wallet per cycle — prevents signal flood
        )

        # Batch preload snapshots and markets for all wallets with trades
        all_market_ids = list({
            tx.market_id
            for trades in wallet_trades_map.values()
            for tx in trades
        })
        snap_cache: dict = {}
        mkt_cache: dict = {}
        if all_market_ids:
            snap_subq = (
                select(MarketSnapshot.market_id, func.max(MarketSnapshot.captured_at).label("latest"))
                .group_by(MarketSnapshot.market_id)
                .where(MarketSnapshot.market_id.in_(all_market_ids))
                .subquery()
            )
            snap_rows = await db.execute(
                select(MarketSnapshot)
                .join(snap_subq, (MarketSnapshot.market_id == snap_subq.c.market_id) & (MarketSnapshot.captured_at == snap_subq.c.latest))
            )
            snap_cache = {s.market_id: s for s in snap_rows.scalars().all()}

            mkt_rows = await db.execute(select(Market).where(Market.id.in_(all_market_ids)))
            mkt_cache = {m.id: m for m in mkt_rows.scalars().all()}

        for w in eligible_wallets:
            # Use cached composite score
            w_composite = wallet_score_cache.get(str(w.id), (0.0, 0.0))[0]
            if w_composite <= 0:
                w_composite = 0.5
            decay_skip_strategies: set[str] = set()

            new_trades = wallet_trades_map.get(w.id, [])

            for tx in new_trades:
                snap = snap_cache.get(tx.market_id)
                if not snap:
                    continue

                market = mkt_cache.get(tx.market_id)
                if not market:
                    continue

                current_price  = float(snap.midpoint or snap.best_bid or 0.5)
                current_spread = float(snap.spread or 0.04)
                current_depth  = float(snap.bid_depth or 200.0)

                for strat_name in copy_strategies:
                    if strat_name in decay_skip_strategies:
                        continue
                    strat_bankroll = strategy_bankrolls.get(strat_name, total_bankroll)
                    if strat_bankroll <= 0 and strat_name != "shadow":
                        continue
                    if strat_name != "shadow" and tx.market_id in cycle_opened_markets:
                        continue

                    tx_side = getattr(tx, "side", None) or "BUY"

                    # SELL copy disabled for direct_copy — data shows BUY 65% win
                    # rate vs SELL 33%. Wallet SELL is often exit/hedge, not direction.
                    # high_conviction SELL allowed only for high-quality wallets.
                    if tx_side == "SELL":
                        if strat_name == "direct_copy":
                            continue
                        if strat_name == "high_conviction" and w_composite < 0.55:
                            continue

                    try:
                        signal = await self.signal_gen.generate_copy_signal(
                            db=db,
                            strategy=strat_name,
                            wallet_id=w.id,
                            market_id=tx.market_id,
                            market_price=current_price,
                            fees_enabled=market.fees_enabled,
                            fee_rate_bps=market.fee_rate_bps,
                            spread=current_spread,
                            wallet_side=tx_side,
                            wallet_score=w_composite,
                            available_depth_usd=current_depth,
                        )
                    except Exception as gen_err:
                        logger.warning("Signal gen error %s/%s: %s", strat_name, str(w.id)[:8], gen_err)
                        continue
                    stats["signals_generated"] += 1
                    stats["strategy_funnel"][strat_name]["signals_generated"] += 1

                    _SKIP_EVALUATE = {"shadow"}
                    if strat_bankroll <= 0 or strat_name in _SKIP_EVALUATE:
                        continue

                    strat = self.strategies[strat_name]

                    if global_pos_count >= _MAX_GLOBAL:
                        continue

                    try:
                        decision = await strat.evaluate(
                            db=db,
                            signal=signal,
                            current_price=current_price,
                            current_spread=current_spread,
                            available_depth=float(snap.bid_depth or 100),
                            book_levels=snap.book_levels,
                            bankroll=strat_bankroll,
                            exposure_pct=global_exposure_pct,
                            wallet_score_cache=wallet_score_cache,
                            snapshot_cache=snap_cache,
                            position_counts=position_counts_preloaded,
                        )
                    except Exception as eval_err:
                        logger.warning(f"{strat_name} evaluate error: {eval_err}")
                        decision = None
                    if decision:
                        stats["decisions_made"] += 1
                        stats["strategy_funnel"][strat_name]["decisions_made"] += 1
                        if decision.decision == "accept":
                            stats["strategy_funnel"][strat_name]["signals_passed_filter"] += 1
                        position = await strat.execute(
                            db=db, signal=signal, decision=decision,
                            book_levels=snap.book_levels,
                            book_snapshot_id=snap.id,
                        )
                        if position:
                            stats["trades_executed"] += 1
                            stats["strategy_funnel"][strat_name]["trades_executed"] += 1
                            global_pos_count += 1
                            position_counts_preloaded[tx.market_id] = position_counts_preloaded.get(tx.market_id, 0) + 1
                            cycle_opened_markets.add(tx.market_id)

        # 6. Dislocation signals (runs even in shadow mode — records shadow decisions)
        rels = await db.execute(
            select(MarketRelationship).where(MarketRelationship.is_active.is_(True))
        )
        disloc_bankroll = strategy_bankrolls.get("dislocation", 0.0)

        for rel in rels.scalars().all():
            snap_a = await _get_latest_snapshot(db, rel.market_a_id)
            snap_b = await _get_latest_snapshot(db, rel.market_b_id)
            if not snap_a or not snap_b:
                continue

            price_a = float(snap_a.midpoint or 0.5)
            price_b = float(snap_b.midpoint or 0.5)

            signal = await self.signal_gen.generate_dislocation_signal(
                db=db,
                relationship_id=rel.id,
                market_a_id=rel.market_a_id,
                market_b_id=rel.market_b_id,
                price_a=price_a,
                price_b=price_b,
                normal_mean=float(rel.normal_spread_mean) if rel.normal_spread_mean else None,
                normal_std=float(rel.normal_spread_std) if rel.normal_spread_std else None,
                spread=float(snap_a.spread or 0.03),
            )

            if signal:
                stats["signals_generated"] += 1
                stats["strategy_funnel"]["dislocation"]["signals_generated"] += 1
                strat    = self.strategies["dislocation"]
                exposure = await check_exposure(db, signal.market_id, 50, disloc_bankroll)

                decision = await strat.evaluate(
                    db=db,
                    signal=signal,
                    current_price=price_b,
                    current_spread=float(snap_b.spread or 0.03),
                    available_depth=float(snap_b.bid_depth or 50),
                    book_levels=snap_b.book_levels,
                    bankroll=disloc_bankroll,   # 0 if shadow → no real trades
                    exposure_pct=exposure.current_exposure_pct,
                )
                if decision:
                    stats["decisions_made"] += 1
                    stats["strategy_funnel"]["dislocation"]["decisions_made"] += 1
                    if decision.decision == "accept":
                        stats["strategy_funnel"]["dislocation"]["signals_passed_filter"] += 1
                    # Only execute if dislocation is active (bankroll > 0)
                    if disloc_bankroll > 0:
                        position = await strat.execute(
                            db=db, signal=signal, decision=decision,
                            book_levels=snap_b.book_levels,
                            book_snapshot_id=snap_b.id,
                        )
                        if position:
                            stats["trades_executed"] += 1
                            stats["strategy_funnel"]["dislocation"]["trades_executed"] += 1

        # 7. Portfolio snapshot
        from app.config import get_settings as _gs
        await compute_portfolio_snapshot(db, starting_balance=_gs().STARTING_BALANCE)

        return stats


async def _get_latest_snapshot(db: AsyncSession, market_id) -> MarketSnapshot | None:
    result = await db.execute(
        select(MarketSnapshot)
        .where(MarketSnapshot.market_id == market_id)
        .order_by(MarketSnapshot.captured_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
