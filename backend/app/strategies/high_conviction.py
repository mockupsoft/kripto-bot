"""Strategy 2: High-Conviction Wallet Copy.

Only copy if:
- wallet score above threshold
- recent form is positive
- market liquidity acceptable
- expected value remains positive after costs
- trade size not suspiciously large relative to market depth
- similar recent trades from same wallet have positive outcome
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.execution.paper_executor import execute_paper_trade
from app.models.paper import PaperPosition
from app.models.signal import SignalDecision, TradeSignal
from app.models.wallet import WalletScore
from app.risk.position_sizer import compute_position_size
from app.signals.signal_filter import FilterResult, SignalFilter
from app.strategies.base import BaseStrategy


class HighConvictionCopyStrategy(BaseStrategy):
    name = "high_conviction"

    def __init__(self, min_composite_score: float | None = None, min_copyability: float = 0.4):
        self._filter = SignalFilter(min_net_edge=0.005, max_signal_age_ms=3000)
        settings = get_settings()
        # Use config value (same as direct_copy) if not explicitly overridden
        self._min_composite = min_composite_score if min_composite_score is not None else float(settings.DIRECT_COPY_MIN_COMPOSITE)
        self._min_copyability = min_copyability

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
        # Extra filter: check wallet quality
        if signal.source_wallet_id:
            score_result = await db.execute(
                select(WalletScore)
                .where(WalletScore.wallet_id == signal.source_wallet_id)
                .order_by(WalletScore.scored_at.desc())
                .limit(1)
            )
            score = score_result.scalar_one_or_none()

            if score is not None and score.composite_score is not None and float(score.composite_score) < self._min_composite:
                result = FilterResult(
                    decision="reject",
                    reason=f"wallet_score_below_threshold: {float(score.composite_score):.2f} < {self._min_composite}",
                    edge_at_decision=float(signal.net_edge or 0),
                    price_drift=0,
                    spread_at_decision=current_spread,
                )
                return await self._filter.record_decision(
                    db=db, signal=signal, result=result,
                    available_bankroll=bankroll, current_exposure_pct=exposure_pct,
                )

            if score is not None and score.copyability_score is not None and float(score.copyability_score) < self._min_copyability:
                result = FilterResult(
                    decision="reject",
                    reason=f"copyability_too_low: {float(score.copyability_score):.2f} < {self._min_copyability}",
                    edge_at_decision=float(signal.net_edge or 0),
                    price_drift=0,
                    spread_at_decision=current_spread,
                )
                return await self._filter.record_decision(
                    db=db, signal=signal, result=result,
                    available_bankroll=bankroll, current_exposure_pct=exposure_pct,
                )

        result = await self._filter.evaluate(
            db=db, signal=signal, current_price=current_price,
            current_spread=current_spread, available_depth_usd=available_depth,
            current_bankroll=bankroll, current_exposure_pct=exposure_pct,
            snapshot_cache=kwargs.get("snapshot_cache"),
            position_counts=kwargs.get("position_counts"),
        )

        w_composite = float(score.composite_score or 0.5) if score and score.composite_score is not None else 0.5
        sizing = compute_position_size(
            model_probability=float(signal.model_probability or 0.5),
            model_confidence=float(signal.model_confidence or 0.5),
            market_price=current_price,
            available_bankroll=bankroll,
            side=signal.side or "BUY",
            wallet_composite=w_composite,
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
            target_structure="single",
            wallet_composite=w_composite,
            signal_edge=float(signal.net_edge or 0),
        )
        return result.position
