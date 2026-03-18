"""Strategy 1: Direct Wallet Copy.

When a tracked wallet opens a new position,
wait for event confirmation, estimate validity after delay,
simulate copying with capped size, exit on configurable conditions.
"""

from __future__ import annotations

import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.execution.paper_executor import execute_paper_trade
from app.models.paper import PaperPosition
from app.models.signal import SignalDecision, TradeSignal
from app.models.wallet import WalletScore
from app.risk.exposure_manager import check_exposure, get_current_bankroll
from app.risk.position_sizer import compute_position_size
from app.signals.signal_filter import FilterResult, SignalFilter
from app.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


class DirectCopyStrategy(BaseStrategy):
    name = "direct_copy"

    def __init__(self):
        self._filter = SignalFilter(min_net_edge=0.0, max_signal_age_ms=5000)
        settings = get_settings()
        self._min_composite = float(settings.DIRECT_COPY_MIN_COMPOSITE)
        self._min_copyability = float(settings.DIRECT_COPY_MIN_COPYABILITY)

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
        wallet_score_cache: dict[str, tuple[float, float]] | None = kwargs.get("wallet_score_cache")

        # Wallet gate: reject low-quality wallets (skip if no source_wallet_id)
        if signal.source_wallet_id:
            composite: float | None = None
            copyability: float | None = None
            wid = str(signal.source_wallet_id)
            if wallet_score_cache and wid in wallet_score_cache:
                composite, copyability = wallet_score_cache[wid]
            else:
                ws_result = await db.execute(
                    select(WalletScore)
                    .where(WalletScore.wallet_id == signal.source_wallet_id)
                    .order_by(WalletScore.scored_at.desc())
                    .limit(1)
                )
                ws = ws_result.scalar_one_or_none()
                if ws:
                    composite = float(ws.composite_score or 0) if ws.composite_score is not None else 0.0
                    copyability = float(ws.copyability_score or 0) if ws.copyability_score is not None else 0.0
                else:
                    logger.debug("wallet_score_missing for %s, proceeding", wid[:8])
                    # Missing score: do NOT reject; veri boşluğu != kötü kalite

            if composite is not None:
                if composite < self._min_composite:
                    reason = f"wallet_score_below_threshold: composite={composite:.2f} min={self._min_composite}"
                    result = FilterResult(
                        decision="reject",
                        reason=reason,
                        edge_at_decision=float(signal.net_edge or 0),
                        price_drift=0,
                        spread_at_decision=current_spread,
                    )
                    return await self._filter.record_decision(
                        db=db, signal=signal, result=result,
                        available_bankroll=bankroll, current_exposure_pct=exposure_pct,
                    )
                if self._min_copyability > 0 and copyability is not None and copyability < self._min_copyability:
                    reason = f"wallet_score_below_threshold: copyability={copyability:.2f} min={self._min_copyability}"
                    result = FilterResult(
                        decision="reject",
                        reason=reason,
                        edge_at_decision=float(signal.net_edge or 0),
                        price_drift=0,
                        spread_at_decision=current_spread,
                    )
                    return await self._filter.record_decision(
                        db=db, signal=signal, result=result,
                        available_bankroll=bankroll, current_exposure_pct=exposure_pct,
                    )

        result = await self._filter.evaluate(
            db=db,
            signal=signal,
            current_price=current_price,
            current_spread=current_spread,
            available_depth_usd=available_depth,
            current_bankroll=bankroll,
            current_exposure_pct=exposure_pct,
            snapshot_cache=kwargs.get("snapshot_cache"),
            position_counts=kwargs.get("position_counts"),
        )

        sizing = compute_position_size(
            model_probability=float(signal.model_probability or 0.5),
            model_confidence=float(signal.model_confidence or 0.5),
            market_price=current_price,
            available_bankroll=bankroll,
            side=signal.side or "BUY",
            wallet_composite=composite if composite is not None else 0.5,
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

        _cb = signal.costs_breakdown or {}
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
            wallet_composite=float(_cb.get("wallet_score", 0)),
            signal_edge=float(signal.net_edge or 0),
        )
        return result.position
