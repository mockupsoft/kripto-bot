"""Stoikov execution heuristic: urgency/inventory bias engine.

NOT a full market-making system. Answers three questions:
1. Urgency pricing: how aggressively cross the spread?
2. Inventory penalty: one leg filled, second getting worse?
3. Passive vs aggressive: post limit or cross spread?
"""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass
class ExecutionBias:
    bias: float  # -1 (strongly passive) to +1 (strongly aggressive)
    reservation_price: float
    urgency_level: str  # 'passive', 'normal', 'urgent', 'critical'


def compute_execution_bias(
    mid_price: float,
    inventory_imbalance: float,
    gamma: float = 0.1,
    sigma: float = 0.05,
    time_remaining_fraction: float = 1.0,
    edge_decay_rate: float = 0.001,
    time_since_signal_ms: int = 0,
) -> ExecutionBias:
    """Compute how aggressively to execute.

    Args:
        mid_price: current midpoint
        inventory_imbalance: [-1, 1] where -1 = need to buy, +1 = need to sell
        gamma: risk aversion coefficient
        sigma: estimated short-term volatility
        time_remaining_fraction: [0, 1] how much time left in the structure's window
        edge_decay_rate: how fast edge erodes per ms
        time_since_signal_ms: ms since signal was generated
    """
    # Stoikov reservation price adjustment: r = s - q * gamma * sigma^2 * (T - t)
    reservation_adjustment = inventory_imbalance * gamma * (sigma ** 2) * time_remaining_fraction
    reservation_price = mid_price - reservation_adjustment

    # Urgency increases as:
    # 1. Time runs out (time_remaining_fraction -> 0)
    # 2. Inventory imbalance grows (incomplete structure)
    # 3. Edge decays over time
    time_urgency = 1 - time_remaining_fraction
    inventory_urgency = abs(inventory_imbalance)
    edge_urgency = min(1.0, edge_decay_rate * time_since_signal_ms)

    raw_bias = (
        0.40 * time_urgency
        + 0.35 * inventory_urgency
        + 0.25 * edge_urgency
    )

    # Normalize to [-1, 1]: positive = aggressive (cross spread), negative = passive (post limit)
    bias = max(-1, min(1, raw_bias * 2 - 0.5))

    if bias > 0.6:
        urgency = "critical"
    elif bias > 0.2:
        urgency = "urgent"
    elif bias > -0.2:
        urgency = "normal"
    else:
        urgency = "passive"

    return ExecutionBias(
        bias=round(bias, 4),
        reservation_price=round(reservation_price, 6),
        urgency_level=urgency,
    )
