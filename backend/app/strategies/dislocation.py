"""Strategy 3: Dislocation Watcher.

Compare related markets, detect spread anomalies, simulate two-leg
synthetic paper structures, reject if second-leg risk too high.

Tunable parameters (via constructor or env-driven settings):
  - min_z_score:           minimum |z| to consider a dislocation real
  - min_confidence_edge:   minimum confidence-adjusted edge after haircuts
  - min_depth_usd:         minimum order book depth per leg
  - max_spread_pct:        max spread as fraction of price
  - max_spread_to_edge:    max ratio of total_cost / raw_edge (cost efficiency gate)
  - min_confidence_factor: hard cutoff — below this, position size = 0
"""

from __future__ import annotations

import os
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.paper_executor import execute_paper_trade
from app.models.paper import PaperPosition
from app.models.signal import SignalDecision, TradeSignal
from app.risk.position_sizer import compute_position_size
from app.signals.signal_filter import FilterResult, SignalFilter
from app.strategies.base import BaseStrategy

# ── Experiment-tunable defaults (override via env vars) ──────────────────────
_Z_THRESHOLD         = float(os.getenv("DISLOCATION_MIN_Z",              "2.0"))
_MIN_CONF_EDGE       = float(os.getenv("DISLOCATION_MIN_CONF_EDGE",       "0.005"))
_MIN_DEPTH           = float(os.getenv("DISLOCATION_MIN_DEPTH",           "20.0"))
_MAX_SPREAD          = float(os.getenv("DISLOCATION_MAX_SPREAD_PCT",      "0.03"))
_MAX_SIGNAL_AGE_MS   = int(os.getenv("DISLOCATION_MAX_SIGNAL_AGE_MS",     "3000"))
_MAX_SPREAD_TO_EDGE  = float(os.getenv("DISLOCATION_MAX_SPREAD_TO_EDGE",  "0.8"))
_MIN_CONF_FACTOR     = float(os.getenv("DISLOCATION_MIN_CONF_FACTOR",     "0.5"))


class DislocationStrategy(BaseStrategy):
    name = "dislocation"

    def __init__(
        self,
        min_z_score: float = _Z_THRESHOLD,
        min_confidence_edge: float = _MIN_CONF_EDGE,
        min_depth_usd: float = _MIN_DEPTH,
        max_spread: float = _MAX_SPREAD,
        max_signal_age_ms: int = _MAX_SIGNAL_AGE_MS,
        max_spread_to_edge: float = _MAX_SPREAD_TO_EDGE,
        min_confidence_factor: float = _MIN_CONF_FACTOR,
    ):
        self.min_z_score = min_z_score
        self.min_confidence_edge = min_confidence_edge
        self.min_depth_usd = min_depth_usd
        self.max_spread_to_edge = max_spread_to_edge
        self.min_confidence_factor = min_confidence_factor

        self._filter = SignalFilter(
            min_net_edge=min_confidence_edge,
            max_spread=max_spread,
            max_signal_age_ms=max_signal_age_ms,
            min_liquidity_usd=min_depth_usd,
        )

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
        # Gate 1: z-score must exceed threshold
        z = float(signal.spread_z_score or 0)
        if abs(z) < self.min_z_score:
            result = FilterResult(
                decision="reject",
                reason=f"z_score_below_threshold: {z:.2f} < {self.min_z_score}",
                edge_at_decision=float(signal.net_edge or 0),
                price_drift=0,
                spread_at_decision=current_spread,
            )
            return await self._filter.record_decision(
                db=db, signal=signal, result=result,
                available_bankroll=bankroll, current_exposure_pct=exposure_pct,
            )

        # Gate 2: confidence-adjusted edge must clear threshold
        conf_factor = float((signal.metadata_ or {}).get("confidence_factor", 1.0))
        raw_edge = float(signal.net_edge or 0)
        conf_adj_edge = raw_edge * conf_factor

        if conf_adj_edge < self.min_confidence_edge:
            result = FilterResult(
                decision="reject",
                reason=(
                    f"confidence_edge_insufficient: adj={conf_adj_edge:.4f} "
                    f"(raw={raw_edge:.4f}, cf={conf_factor:.2f}) < {self.min_confidence_edge}"
                ),
                edge_at_decision=conf_adj_edge,
                price_drift=0,
                spread_at_decision=current_spread,
            )
            return await self._filter.record_decision(
                db=db, signal=signal, result=result,
                available_bankroll=bankroll, current_exposure_pct=exposure_pct,
            )

        # Gate 3: spread-to-edge ratio — cost must not dominate edge
        costs_breakdown = signal.costs_breakdown or {}
        total_cost = float(costs_breakdown.get("total", 0))
        signal_raw_edge = float(signal.raw_edge or 0)
        spread_to_edge = total_cost / max(signal_raw_edge, 1e-8)
        if self.max_spread_to_edge > 0 and spread_to_edge > self.max_spread_to_edge:
            result = FilterResult(
                decision="reject",
                reason=(
                    f"spread_to_edge_too_high: {spread_to_edge:.3f} > {self.max_spread_to_edge} "
                    f"(cost={total_cost:.4f} / raw_edge={signal_raw_edge:.4f})"
                ),
                edge_at_decision=conf_adj_edge,
                price_drift=0,
                spread_at_decision=current_spread,
            )
            return await self._filter.record_decision(
                db=db, signal=signal, result=result,
                available_bankroll=bankroll, current_exposure_pct=exposure_pct,
            )

        # Gate 4: confidence factor hard cutoff — below 0.5 means conditions too uncertain
        if conf_factor < self.min_confidence_factor:
            result = FilterResult(
                decision="reject",
                reason=f"confidence_factor_too_low: {conf_factor:.3f} < {self.min_confidence_factor} (hard cutoff)",
                edge_at_decision=conf_adj_edge,
                price_drift=0,
                spread_at_decision=current_spread,
            )
            return await self._filter.record_decision(
                db=db, signal=signal, result=result,
                available_bankroll=bankroll, current_exposure_pct=exposure_pct,
            )

        # All gates passed — run through standard filter (liquidity, staleness, exposure)
        result = await self._filter.evaluate(
            db=db, signal=signal, current_price=current_price,
            current_spread=current_spread, available_depth_usd=available_depth,
            current_bankroll=bankroll, current_exposure_pct=exposure_pct,
        )

        # Sizing: Kelly × confidence_factor (smaller position when confidence is lower)
        sizing = compute_position_size(
            model_probability=float(signal.model_probability or 0.5),
            model_confidence=float(signal.model_confidence or 0.7),
            market_price=current_price,
            available_bankroll=bankroll,
            confidence_factor=conf_factor,
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

        group_id = uuid4()
        half_size = float(decision.proposed_size or 0) / 2

        # Leg 1: BUY the underpriced market
        result_1 = await execute_paper_trade(
            db=db,
            signal_id=signal.id,
            decision_id=decision.id,
            market_id=signal.market_id,
            side="BUY",
            outcome="Yes",
            requested_size=half_size,
            market_price=float(signal.market_price or 0.5),
            strategy=self.name,
            book_levels=book_levels,
            book_snapshot_id=book_snapshot_id,
            position_group_id=group_id,
            leg_index=1,
            is_hedge_leg=False,
            target_structure="long_short_spread",
        )

        # Leg 2: SELL the overpriced market (hedge leg)
        if signal.related_market_id and result_1.position:
            await execute_paper_trade(
                db=db,
                signal_id=signal.id,
                decision_id=decision.id,
                market_id=signal.related_market_id,
                side="SELL",
                outcome="No",
                requested_size=half_size,
                market_price=float(signal.market_price or 0.5) + abs(float(signal.raw_edge or 0)),
                strategy=self.name,
                book_levels=book_levels,
                position_group_id=group_id,
                leg_index=2,
                is_hedge_leg=True,
                target_structure="long_short_spread",
            )

        return result_1.position
