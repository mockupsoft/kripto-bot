"""Partial fill and failure simulation.

Models the probability of full/partial/failed fills based on
order size, market depth, and execution urgency.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass
class FillSimulation:
    fill_probability: float
    expected_fill_pct: float
    actual_fill_pct: float
    is_full_fill: bool
    failure_reason: str | None


def simulate_fill(
    requested_size: float,
    available_depth: float,
    urgency_bias: float = 0.0,
    base_fill_rate: float = 0.85,
    rng: random.Random | None = None,
) -> FillSimulation:
    """Simulate whether an order fills fully, partially, or fails.

    Args:
        requested_size: how much we want to trade
        available_depth: estimated depth on our side of the book
        urgency_bias: [-1, 1] from Stoikov; positive = aggressive = better fill rate
        base_fill_rate: baseline probability of complete fill
        rng: random generator for reproducibility
    """
    rng = rng or random.Random()

    # Depth ratio: if we're trying to fill more than available, lower fill rate
    depth_ratio = requested_size / max(available_depth, 1)

    if depth_ratio > 1.0:
        # Requesting more than available: guaranteed partial
        fill_prob = 0.0
        expected_pct = available_depth / requested_size
    elif depth_ratio > 0.5:
        # Eating more than half the book: risky
        fill_prob = base_fill_rate * (1 - (depth_ratio - 0.5))
        expected_pct = 0.7 + 0.3 * (1 - depth_ratio)
    else:
        fill_prob = base_fill_rate + urgency_bias * 0.1
        expected_pct = 0.95

    fill_prob = max(0, min(1, fill_prob))
    expected_pct = max(0, min(1, expected_pct))

    roll = rng.random()
    if roll < fill_prob:
        actual_pct = expected_pct + rng.gauss(0, 0.05)
        actual_pct = max(0.5, min(1.0, actual_pct))
        failure_reason = None
    elif roll < fill_prob + 0.15:
        actual_pct = rng.uniform(0.3, expected_pct)
        failure_reason = "partial_fill_depth_exhausted"
    else:
        actual_pct = 0.0
        failure_reason = "fill_failed_no_liquidity"

    return FillSimulation(
        fill_probability=round(fill_prob, 4),
        expected_fill_pct=round(expected_pct, 4),
        actual_fill_pct=round(actual_pct, 4),
        is_full_fill=actual_pct >= 0.95,
        failure_reason=failure_reason,
    )
