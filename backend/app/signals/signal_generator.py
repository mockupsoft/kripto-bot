"""Signal generator: combines Bayesian, Edge, and Spread models to emit trade signals."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.signal import TradeSignal
from app.signals.calibration import HybridProbabilityCalibrator
from app.signals.bayesian import BayesianEstimator, BayesianEstimate
from app.signals.edge import calculate_edge, EdgeResult
from app.signals.spread import SpreadDetector, SpreadSignal


class SignalGenerator:
    """Orchestrates the signal pipeline for both copy and dislocation strategies."""

    def __init__(self):
        self._estimators: dict[str, BayesianEstimator] = {}
        self._spread_detector = SpreadDetector(window_size=100, z_threshold=2.0)
        self._calibrator = HybridProbabilityCalibrator()

    def get_estimator(self, market_id: str, market_price: float = 0.5) -> BayesianEstimator:
        if market_id not in self._estimators:
            # Anchor prior to market price so model starts near market consensus.
            # Beta(a, b) with mean = market_price: use total=4 (weak prior).
            total = 4.0
            a = max(0.5, market_price * total)
            b = max(0.5, (1 - market_price) * total)
            self._estimators[market_id] = BayesianEstimator(prior_alpha=a, prior_beta=b)
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
        wallet_side: str = "BUY",
        wallet_score: float = 0.5,
        available_depth_usd: float = 200.0,
    ) -> TradeSignal:
        settings = get_settings()

        # Direct wallet-boost copy model (heuristic alpha, not Bayesian).
        # We copy the wallet's direction: wallet BUY → we BUY, wallet SELL → we SELL.
        # Edge = wallet_quality_boost, applied in the wallet's direction.
        _GATE_THRESHOLD = float(settings.DIRECT_COPY_MIN_COMPOSITE)  # 0.35
        _BOOST = float(os.getenv("COPY_EDGE_BOOST", "0.12"))
        wallet_edge = max(0.0, (wallet_score - _GATE_THRESHOLD) * _BOOST)

        side = wallet_side.upper() if wallet_side else "BUY"
        if side == "SELL":
            copy_prob = max(0.01, min(0.99, market_price - wallet_edge))
        else:
            copy_prob = max(0.01, min(0.99, market_price + wallet_edge))

        copy_confidence = min(1.0, 0.3 + wallet_score * 0.7)

        # Edge is always the absolute divergence — positive means "trade is worth it"
        # regardless of direction. calculate_edge uses signed (q - p), so for SELL
        # we flip: pass (1 - copy_prob) vs (1 - market_price) to get positive edge.
        if side == "SELL":
            edge = calculate_edge(
                model_probability=1.0 - copy_prob,
                market_price=1.0 - market_price,
                model_confidence=copy_confidence,
                fees_enabled=fees_enabled,
                fee_rate_bps=fee_rate_bps,
                spread=spread,
                available_depth_usd=available_depth_usd,
            )
        else:
            edge = calculate_edge(
                model_probability=copy_prob,
                market_price=market_price,
                model_confidence=copy_confidence,
                fees_enabled=fees_enabled,
                fee_rate_bps=fee_rate_bps,
                spread=spread,
                available_depth_usd=available_depth_usd,
            )

        from app.models.base import new_uuid
        signal = TradeSignal(
            id=new_uuid(),
            strategy=strategy,
            source_type="wallet_copy",
            source_wallet_id=wallet_id,
            market_id=market_id,
            created_at=datetime.now(timezone.utc),
            side=side,
            model_probability=copy_prob,
            model_confidence=copy_confidence,
            market_price=market_price,
            raw_edge=edge.raw_edge,
            net_edge=edge.net_edge,
            costs_breakdown={
                **edge.costs_breakdown,
                "calibration_method": "copy_direct",
                "model_probability_raw": round(copy_prob, 6),
                "wallet_score": round(wallet_score, 4),
                "wallet_edge_boost": round(wallet_edge, 6),
                "wallet_side": side,
            },
        )
        db.add(signal)
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
        calibrated_prob = model_prob

        edge = calculate_edge(
            model_probability=calibrated_prob,
            market_price=underpriced_price,
            model_confidence=0.7,
            fees_enabled=fees_enabled,
            fee_rate_bps=fee_rate_bps,
            spread=spread,
        )

        from app.models.base import new_uuid
        signal = TradeSignal(
            id=new_uuid(),
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
            costs_breakdown={
                **edge.costs_breakdown,
                "calibrated_probability": round(calibrated_prob, 6),
                "calibration_method": "identity_dislocation",
                "model_probability_raw": round(float(model_prob), 6),
            },
        )
        db.add(signal)
        return signal
