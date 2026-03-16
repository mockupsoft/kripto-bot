"""Bayesian probability estimator.

NOT an oracle. Outputs model_estimate +/- confidence.
Uses Beta distribution conjugate updates.
"""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass
class BayesianEstimate:
    model_probability: float
    confidence: float
    alpha: float
    beta_param: float


class BayesianEstimator:
    """Per-market Bayesian estimator using Beta(alpha, beta) prior.

    Updates with observable market signals to produce a probability estimate
    with an honest confidence band.
    """

    def __init__(self, prior_alpha: float = 2.0, prior_beta: float = 2.0):
        self.alpha = prior_alpha
        self.beta_param = prior_beta
        self._last_update_age = 0

    def update(
        self,
        spot_move_direction: float,
        volatility_signal: float,
        order_book_imbalance: float,
        repricing_speed: float,
        volume_signal: float = 0.0,
    ) -> BayesianEstimate:
        """Update the estimate with new observable data.

        Args:
            spot_move_direction: [-1, 1] direction and magnitude of underlying move
            volatility_signal: [0, 1] short-term volatility spike
            order_book_imbalance: [-1, 1] bid vs ask pressure
            repricing_speed: [0, 1] how fast related markets are repricing
            volume_signal: [0, 1] unusual volume indicator
        """
        # Aggregate signal strength: weighted combination of inputs
        signal = (
            0.35 * spot_move_direction
            + 0.20 * order_book_imbalance
            + 0.20 * repricing_speed
            + 0.15 * volatility_signal
            + 0.10 * volume_signal
        )

        # Pseudo-observations: positive signal adds to alpha, negative to beta
        observation_weight = 0.5
        if signal > 0:
            self.alpha += observation_weight * abs(signal)
        else:
            self.beta_param += observation_weight * abs(signal)

        # Posterior mean
        prob = self.alpha / (self.alpha + self.beta_param)

        # Confidence: narrower posterior = higher confidence
        # Use Beta distribution variance: var = ab / ((a+b)^2 * (a+b+1))
        total = self.alpha + self.beta_param
        variance = (self.alpha * self.beta_param) / (total ** 2 * (total + 1))
        std = math.sqrt(variance)

        # Confidence: inverse of std, normalized to [0, 1]
        # With total=4 (prior), std ≈ 0.2. With total=100, std ≈ 0.05.
        confidence = max(0, min(1, 1 - std * 4))

        self._last_update_age = 0

        return BayesianEstimate(
            model_probability=round(prob, 6),
            confidence=round(confidence, 4),
            alpha=round(self.alpha, 4),
            beta_param=round(self.beta_param, 4),
        )

    def age_estimate(self, seconds_elapsed: float) -> None:
        """Confidence decays over time if no new data arrives.

        The estimate gets stale, not more certain.
        """
        self._last_update_age += seconds_elapsed
        # Slowly shrink alpha and beta back toward prior (mean reversion)
        decay = 0.99 ** (seconds_elapsed / 60)
        self.alpha = max(1.0, self.alpha * decay)
        self.beta_param = max(1.0, self.beta_param * decay)

    @property
    def current_estimate(self) -> float:
        return self.alpha / (self.alpha + self.beta_param)

    @property
    def current_confidence(self) -> float:
        total = self.alpha + self.beta_param
        variance = (self.alpha * self.beta_param) / (total ** 2 * (total + 1))
        return max(0, min(1, 1 - math.sqrt(variance) * 4))
