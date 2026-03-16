"""Latency simulation: models detection, decision, and execution delays."""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass
class DelaySimulation:
    detection_delay_ms: int
    decision_delay_ms: int
    execution_delay_ms: int
    total_delay_ms: int


def simulate_delay(
    detection_base_ms: int = 500,
    decision_base_ms: int = 200,
    execution_base_ms: int = 300,
    jitter_pct: float = 0.3,
    rng: random.Random | None = None,
) -> DelaySimulation:
    """Simulate realistic pipeline delays with random jitter.

    Args:
        detection_base_ms: base time to detect the wallet's trade
        decision_base_ms: base time for signal evaluation
        execution_base_ms: base time for order simulation
        jitter_pct: random variation (e.g. 0.3 = ±30%)
        rng: random generator for reproducibility
    """
    rng = rng or random.Random()

    def _jitter(base: int) -> int:
        factor = 1 + rng.uniform(-jitter_pct, jitter_pct)
        return max(1, int(base * factor))

    det = _jitter(detection_base_ms)
    dec = _jitter(decision_base_ms)
    exe = _jitter(execution_base_ms)

    return DelaySimulation(
        detection_delay_ms=det,
        decision_delay_ms=dec,
        execution_delay_ms=exe,
        total_delay_ms=det + dec + exe,
    )
