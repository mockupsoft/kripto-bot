"""Scenario engine: ideal / realistic / pessimistic execution assumptions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ScenarioConfig:
    name: str
    detection_delay_ms: int
    decision_delay_ms: int
    execution_delay_ms: int
    base_slippage_bps: int
    volatility_multiplier: float
    fill_rate: float
    fee_multiplier: float


SCENARIOS = {
    "ideal": ScenarioConfig(
        name="ideal",
        detection_delay_ms=100,
        decision_delay_ms=50,
        execution_delay_ms=100,
        base_slippage_bps=5,
        volatility_multiplier=1.0,
        fill_rate=0.98,
        fee_multiplier=1.0,
    ),
    "realistic": ScenarioConfig(
        name="realistic",
        detection_delay_ms=500,
        decision_delay_ms=200,
        execution_delay_ms=300,
        base_slippage_bps=20,
        volatility_multiplier=1.5,
        fill_rate=0.85,
        fee_multiplier=1.0,
    ),
    "pessimistic": ScenarioConfig(
        name="pessimistic",
        detection_delay_ms=1000,
        decision_delay_ms=400,
        execution_delay_ms=600,
        base_slippage_bps=40,
        volatility_multiplier=2.5,
        fill_rate=0.65,
        fee_multiplier=1.5,
    ),
}


def get_scenario(name: str) -> ScenarioConfig:
    return SCENARIOS.get(name, SCENARIOS["realistic"])


def get_all_scenarios() -> list[ScenarioConfig]:
    return list(SCENARIOS.values())
