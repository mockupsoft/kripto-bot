"""Strategy 4: Shadow Mode.

Do not simulate trades immediately. Log "would-have-entered" opportunities.
Compare later against actual outcomes. Validates signal quality before
allowing full paper entries.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.paper import PaperPosition
from app.models.signal import SignalDecision, TradeSignal
from app.risk.position_sizer import compute_position_size
from app.signals.signal_filter import FilterResult, SignalFilter
from app.strategies.base import BaseStrategy


class ShadowModeStrategy(BaseStrategy):
    name = "shadow"

    def __init__(self):
        self._filter = SignalFilter(min_net_edge=-0.1, max_signal_age_ms=10000)

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
        **kwargs: object,
    ) -> SignalDecision | None:
        """Always logs the opportunity as 'shadow', never accepts for execution."""
        sizing = compute_position_size(
            model_probability=float(signal.model_probability or 0.5),
            model_confidence=float(signal.model_confidence or 0.5),
            market_price=current_price,
            available_bankroll=bankroll,
        )

        result = FilterResult(
            decision="shadow",
            reason="shadow_mode_observation_only",
            edge_at_decision=float(signal.net_edge or 0),
            price_drift=current_price - float(signal.market_price or current_price),
            spread_at_decision=current_spread,
        )

        return await self._filter.record_decision(
            db=db, signal=signal, result=result,
            kelly_fraction=sizing.capped_fraction,
            proposed_size=sizing.proposed_size_usd,
            available_bankroll=bankroll, current_exposure_pct=exposure_pct,
        )

    async def execute(
        self,
        db: AsyncSession,
        signal: TradeSignal,
        decision: SignalDecision,
        book_levels: dict | None,
        book_snapshot_id: int | None,
    ) -> PaperPosition | None:
        """Shadow mode never executes."""
        return None
