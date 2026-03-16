"""Signal generator: combines Bayesian, Edge, and Spread models to emit trade signals."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.signal import TradeSignal
from app.signals.bayesian import BayesianEstimator, BayesianEstimate
from app.signals.edge import calculate_edge, EdgeResult
from app.signals.spread import SpreadDetector, SpreadSignal


class SignalGenerator:
    """Orchestrates the signal pipeline for both copy and dislocation strategies."""

    def __init__(self):
        self._estimators: dict[str, BayesianEstimator] = {}
        self._spread_detector = SpreadDetector(window_size=100, z_threshold=2.0)

    def get_estimator(self, market_id: str) -> BayesianEstimator:
        if market_id not in self._estimators:
            self._estimators[market_id] = BayesianEstimator()
        return self._estimators[market_id]

    async def generate_copy_signal(
        self,
        db: AsyncSession,
        strategy: str,
        wallet_id: UUID,
        market_id: UUID,
        market_price: float,
        fees_enabled: bool,
        fee_rate_bps: int,
        spread: float,
        spot_move: float = 0.0,
        volatility: float = 0.0,
        book_imbalance: float = 0.0,
        repricing_speed: float = 0.0,
        wallet_score: float = 0.5,  # [0, 1] composite wallet quality score
        available_depth_usd: float = 200.0,  # for Layer-3 liquidity factor
    ) -> TradeSignal:
        estimator = self.get_estimator(str(market_id))

        # Wallet score as behavioral signal — maps to [-1, 1] but deliberately dampened.
        # Without a real spot feed this is the only dynamic Bayesian input, so we reduce
        # its weight to avoid probability divergence (edge inflation).
        # Scale: score=0.8 → imbalance=+0.30  (not 0.60 as before)
        #        score=0.2 → imbalance=-0.30
        # This keeps raw_edge within a realistic ±0.10 band before the MAX_RAW_EDGE cap.
        effective_book_imbalance = (
            book_imbalance if book_imbalance != 0
            else (wallet_score - 0.5) * 0.6   # dampened: was *2
        )
        effective_repricing = (
            repricing_speed if repricing_speed != 0
            else max(0.0, (wallet_score - 0.4) * 0.5)   # dampened: was *1.5
        )

        estimate = estimator.update(
            spot_move_direction=spot_move,
            volatility_signal=volatility,
            order_book_imbalance=effective_book_imbalance,
            repricing_speed=effective_repricing,
        )

        edge = calculate_edge(
            model_probability=estimate.model_probability,
            market_price=market_price,
            model_confidence=estimate.confidence,
            fees_enabled=fees_enabled,
            fee_rate_bps=fee_rate_bps,
            spread=spread,
            available_depth_usd=available_depth_usd,
        )

        side = "BUY" if estimate.model_probability > market_price else "SELL"

        signal = TradeSignal(
            strategy=strategy,
            source_type="wallet_copy",
            source_wallet_id=wallet_id,
            market_id=market_id,
            created_at=datetime.now(timezone.utc),
            side=side,
            model_probability=estimate.model_probability,
            model_confidence=estimate.confidence,
            market_price=market_price,
            raw_edge=edge.raw_edge,
            net_edge=edge.net_edge,
            costs_breakdown=edge.costs_breakdown,
        )
        db.add(signal)
        await db.flush()
        return signal

    async def generate_dislocation_signal(
        self,
        db: AsyncSession,
        relationship_id: UUID,
        market_a_id: UUID,
        market_b_id: UUID,
        price_a: float,
        price_b: float,
        normal_mean: float | None,
        normal_std: float | None,
        fees_enabled: bool = True,
        fee_rate_bps: int = 250,
        spread: float = 0.04,
    ) -> TradeSignal | None:
        spread_signal = self._spread_detector.update(
            relationship_id=str(relationship_id),
            market_a_id=str(market_a_id),
            market_b_id=str(market_b_id),
            price_a=price_a,
            price_b=price_b,
            normal_spread_mean=normal_mean,
            normal_spread_std=normal_std,
        )

        if not spread_signal.is_dislocation:
            return None

        underpriced_market = market_b_id if spread_signal.z_score > 0 else market_a_id
        underpriced_price = price_b if spread_signal.z_score > 0 else price_a

        # Fair value: mean of both prices (simplistic but honest)
        model_prob = (price_a + price_b) / 2

        edge = calculate_edge(
            model_probability=model_prob,
            market_price=underpriced_price,
            model_confidence=0.7,
            fees_enabled=fees_enabled,
            fee_rate_bps=fee_rate_bps,
            spread=spread,
        )

        signal = TradeSignal(
            strategy="dislocation",
            source_type="spread_anomaly",
            market_id=underpriced_market,
            related_market_id=market_a_id if underpriced_market == market_b_id else market_b_id,
            relationship_id=relationship_id,
            created_at=datetime.now(timezone.utc),
            side="BUY",
            model_probability=model_prob,
            model_confidence=0.7,
            market_price=underpriced_price,
            raw_edge=edge.raw_edge,
            net_edge=edge.net_edge,
            spread_z_score=spread_signal.z_score,
            costs_breakdown=edge.costs_breakdown,
        )
        db.add(signal)
        await db.flush()
        return signal
