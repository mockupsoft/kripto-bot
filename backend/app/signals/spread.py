"""Cross-market spread z-score detector.

Reads market pairs from market_relationships table.
Computes rolling spread statistics and flags dislocations.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math


@dataclass
class SpreadSignal:
    market_a_id: str
    market_b_id: str
    relationship_id: str
    spread: float
    z_score: float
    mean: float
    std: float
    is_dislocation: bool


class SpreadDetector:
    """Tracks spread between related market pairs and detects anomalies."""

    def __init__(self, window_size: int = 100, z_threshold: float = 2.0):
        self._window_size = window_size
        self._z_threshold = z_threshold
        self._history: dict[str, deque[float]] = {}

    def update(
        self,
        relationship_id: str,
        market_a_id: str,
        market_b_id: str,
        price_a: float,
        price_b: float,
        normal_spread_mean: float | None = None,
        normal_spread_std: float | None = None,
    ) -> SpreadSignal:
        """Record a new spread observation and check for dislocation.

        If historical mean/std are provided from the relationship record,
        they are used as the baseline. Otherwise, the detector builds its own
        rolling statistics.
        """
        spread = price_a - price_b

        if relationship_id not in self._history:
            self._history[relationship_id] = deque(maxlen=self._window_size)
        self._history[relationship_id].append(spread)

        history = self._history[relationship_id]

        if normal_spread_mean is not None and normal_spread_std is not None:
            mean = normal_spread_mean
            std = normal_spread_std
        elif len(history) >= 10:
            mean = sum(history) / len(history)
            std = math.sqrt(sum((s - mean) ** 2 for s in history) / len(history))
        else:
            return SpreadSignal(
                market_a_id=market_a_id,
                market_b_id=market_b_id,
                relationship_id=relationship_id,
                spread=round(spread, 6),
                z_score=0.0,
                mean=0.0,
                std=0.0,
                is_dislocation=False,
            )

        z_score = (spread - mean) / max(std, 1e-8)

        return SpreadSignal(
            market_a_id=market_a_id,
            market_b_id=market_b_id,
            relationship_id=relationship_id,
            spread=round(spread, 6),
            z_score=round(z_score, 4),
            mean=round(mean, 6),
            std=round(std, 6),
            is_dislocation=abs(z_score) > self._z_threshold,
        )

    def get_rolling_stats(self, relationship_id: str) -> tuple[float, float]:
        history = self._history.get(relationship_id, deque())
        if len(history) < 2:
            return 0.0, 0.0
        mean = sum(history) / len(history)
        std = math.sqrt(sum((s - mean) ** 2 for s in history) / len(history))
        return mean, std
