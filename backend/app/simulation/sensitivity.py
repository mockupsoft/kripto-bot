"""Latency and slippage sensitivity sweeps.

Answers: "Would this still be profitable with +500ms delay?"
         "Would this still be profitable with 2x slippage?"
"""

from __future__ import annotations

from dataclasses import dataclass

from app.simulation.monte_carlo import run_monte_carlo, MonteCarloResult


@dataclass
class SensitivityPoint:
    parameter_name: str
    parameter_value: float
    median_equity: float
    ruin_probability: float
    p5_equity: float


def latency_sensitivity_sweep(
    base_delay_ms: int = 800,
    delays_to_test: list[int] | None = None,
    **mc_kwargs,
) -> list[SensitivityPoint]:
    """Test strategy viability across different total latencies."""
    if delays_to_test is None:
        delays_to_test = [200, 500, 800, 1000, 1500, 2000, 3000]

    results = []
    for delay in delays_to_test:
        # More delay = worse fill rate and more slippage
        delay_factor = delay / base_delay_ms
        adjusted_fill_rate = max(0.3, mc_kwargs.get("fill_rate", 0.85) / delay_factor)
        adjusted_slippage = mc_kwargs.get("avg_slippage_pct", 0.005) * delay_factor

        mc = run_monte_carlo(
            fill_rate=adjusted_fill_rate,
            avg_slippage_pct=adjusted_slippage,
            **{k: v for k, v in mc_kwargs.items() if k not in ("fill_rate", "avg_slippage_pct")},
        )

        results.append(SensitivityPoint(
            parameter_name="total_delay_ms",
            parameter_value=delay,
            median_equity=mc.median_final_equity,
            ruin_probability=mc.ruin_probability,
            p5_equity=mc.percentiles["P5"],
        ))

    return results


def slippage_sensitivity_sweep(
    base_slippage_pct: float = 0.005,
    multipliers: list[float] | None = None,
    **mc_kwargs,
) -> list[SensitivityPoint]:
    """Test strategy viability across different slippage levels."""
    if multipliers is None:
        multipliers = [0.5, 1.0, 1.5, 2.0, 3.0, 5.0]

    results = []
    for mult in multipliers:
        mc = run_monte_carlo(
            avg_slippage_pct=base_slippage_pct * mult,
            **{k: v for k, v in mc_kwargs.items() if k != "avg_slippage_pct"},
        )

        results.append(SensitivityPoint(
            parameter_name="slippage_multiplier",
            parameter_value=mult,
            median_equity=mc.median_final_equity,
            ruin_probability=mc.ruin_probability,
            p5_equity=mc.percentiles["P5"],
        ))

    return results
