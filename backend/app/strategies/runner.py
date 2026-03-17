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
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.pnl import compute_portfolio_snapshot
from app.ingestion.event_normalizer import process_pending_events
from app.intelligence.wallet_scorer import score_and_persist
from app.intelligence.wallet_tracker import detect_new_trades, get_tracked_wallets
from app.models.market import Market, MarketRelationship, MarketSnapshot
from app.models.signal import TradeSignal
from app.models.trade import WalletTransaction
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

        # 1. Process pending raw events into domain tables
        stats["events_processed"] = await process_pending_events(db)

        # 2. Check global kill switch
        kill = await check_kill_switch(db)
        if kill.is_active:
            stats["kill_switch"] = kill.reason
            return stats

        # 3. Score tracked wallets
        wallets = await get_tracked_wallets(db)
        for w in wallets:
            await score_and_persist(db, w.id)

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

        # 5. Detect new trades from tracked wallets — with alpha decay gate
        since_cutoff = datetime.now(timezone.utc) - timedelta(minutes=2)

        for w in wallets:
            # Alpha decay kill-switch: skip wallet if decay risk is critical
            try:
                from app.intelligence.wallet_alpha import detect_alpha_decay
                decay = await detect_alpha_decay(db, w.id)
                if decay.get("decay_alert") and decay.get("reason", "") != "insufficient_data":
                    # Decay detected — still allow direct_copy but skip leader_copy
                    decay_skip_strategies = {"leader_copy", "high_conviction"}
                else:
                    decay_skip_strategies = set()
            except Exception:
                decay_skip_strategies = set()
            new_trades = await detect_new_trades(db, w.id, since=since_cutoff)
            # Only process trades that are linked to a known market
            new_trades = [t for t in new_trades if t.market_id is not None]

            # Fetch latest wallet score for Bayesian signal injection
            ws_result = await db.execute(
                select(WalletScore)
                .where(WalletScore.wallet_id == w.id)
                .order_by(WalletScore.scored_at.desc())
                .limit(1)
            )
            ws = ws_result.scalar_one_or_none()
            w_composite = float(ws.composite_score or 0.5) if ws else 0.5

            for tx in new_trades[:2]:  # max 2 trades per wallet per cycle — prevents position flood
                snap = await _get_latest_snapshot(db, tx.market_id)
                if not snap:
                    continue

                market = await db.get(Market, tx.market_id)
                if not market:
                    continue

                current_price  = float(snap.midpoint or snap.best_bid or 0.5)
                current_spread = float(snap.spread or 0.04)
                current_depth  = float(snap.bid_depth or 200.0)

                for strat_name in copy_strategies:
                    # Alpha decay kill-switch: skip high-conviction strategies for decaying wallets
                    if strat_name in decay_skip_strategies:
                        continue
                    strat_bankroll = strategy_bankrolls.get(strat_name, total_bankroll)
                    # Skip shadow/paused strategies entirely — no signal generation, no DB noise
                    if strat_bankroll <= 0 and strat_name != "shadow":
                        continue

                    signal = await self.signal_gen.generate_copy_signal(
                        db=db,
                        strategy=strat_name,
                        wallet_id=w.id,
                        market_id=tx.market_id,
                        market_price=current_price,
                        fees_enabled=market.fees_enabled,
                        fee_rate_bps=market.fee_rate_bps,
                        spread=current_spread,
                        wallet_score=w_composite,
                        available_depth_usd=current_depth,   # Layer-3 liquidity input
                    )
                    stats["signals_generated"] += 1
                    stats["strategy_funnel"][strat_name]["signals_generated"] += 1

                    strat   = self.strategies[strat_name]
                    exposure = await check_exposure(db, tx.market_id, 50, strat_bankroll, wallet_id=w.id)

                    try:
                        decision = await strat.evaluate(
                            db=db,
                            signal=signal,
                            current_price=current_price,
                            current_spread=current_spread,
                            available_depth=float(snap.bid_depth or 100),
                            book_levels=snap.book_levels,
                            bankroll=strat_bankroll,
                            exposure_pct=exposure.current_exposure_pct,
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
        await compute_portfolio_snapshot(db, starting_balance=900.0)

        return stats


async def _get_latest_snapshot(db: AsyncSession, market_id) -> MarketSnapshot | None:
    result = await db.execute(
        select(MarketSnapshot)
        .where(MarketSnapshot.market_id == market_id)
        .order_by(MarketSnapshot.captured_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
