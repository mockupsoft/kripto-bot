"""Strategy 1: Direct Wallet Copy.

When a tracked wallet opens a new position,
wait for event confirmation, estimate validity after delay,
simulate copying with capped size, exit on configurable conditions.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.paper_executor import execute_paper_trade
from app.models.paper import PaperPosition
from app.models.signal import SignalDecision, TradeSignal
from app.risk.exposure_manager import check_exposure, get_current_bankroll
from app.risk.position_sizer import compute_position_size
from app.signals.signal_filter import FilterResult, SignalFilter
from app.strategies.base import BaseStrategy


class DirectCopyStrategy(BaseStrategy):
    name = "direct_copy"

    def __init__(self):
        self._filter = SignalFilter(min_net_edge=0.0, max_signal_age_ms=5000)

    async def evaluate(
        self,
        db: AsyncSession,
        signal: TradeSignal,
        current_price: float,
        current_spread: float,
        available_depth: float,
        book_levels: dict | None,
        bankroll: float,
        exposure_pct: float,
    ) -> SignalDecision | None:
        result = await self._filter.evaluate(
            db=db,
            signal=signal,
            current_price=current_price,
            current_spread=current_spread,
            available_depth_usd=available_depth,
            current_bankroll=bankroll,
            current_exposure_pct=exposure_pct,
        )

        sizing = compute_position_size(
            model_probability=float(signal.model_probability or 0.5),
            model_confidence=float(signal.model_confidence or 0.5),
            market_price=current_price,
            available_bankroll=bankroll,
            side=signal.side or "BUY",
        )

        decision = await self._filter.record_decision(
            db=db,
            signal=signal,
            result=result,
            kelly_fraction=sizing.capped_fraction,
            proposed_size=sizing.proposed_size_usd,
            available_bankroll=bankroll,
            current_exposure_pct=exposure_pct,
        )
        return decision

    async def execute(
        self,
        db: AsyncSession,
        signal: TradeSignal,
        decision: SignalDecision,
        book_levels: dict | None,
        book_snapshot_id: int | None,
    ) -> PaperPosition | None:
        if decision.decision != "accept":
            return None

        result = await execute_paper_trade(
            db=db,
            signal_id=signal.id,
            decision_id=decision.id,
            market_id=signal.market_id,
            side=signal.side or "BUY",
            outcome="Yes",
            requested_size=float(decision.proposed_size or 0),
            market_price=float(signal.market_price or 0.5),
            strategy=self.name,
            source_wallet_id=signal.source_wallet_id,
            book_levels=book_levels,
            book_snapshot_id=book_snapshot_id,
            fees_enabled=True,
            fee_rate_bps=250,
            available_depth=100.0,
            target_structure="single",
        )
        return result.position
