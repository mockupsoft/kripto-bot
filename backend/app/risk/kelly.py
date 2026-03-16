"""Kelly Criterion position sizing.

Fractional Kelly with configurable lambda for conservative sizing.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class KellyResult:
    optimal_fraction: float
    capped_fraction: float
    proposed_size_usd: float
    sizing_reason: str


def kelly_size(
    win_probability: float,
    payoff_ratio: float = 1.0,
    kelly_fraction: float = 0.25,
    max_position_pct: float = 0.08,
    available_bankroll: float = 900.0,
    model_confidence: float = 1.0,
) -> KellyResult:
    """Compute position size using fractional Kelly criterion.

    Args:
        win_probability: estimated probability of profit (p)
        payoff_ratio: win amount / loss amount (b)
        kelly_fraction: lambda scaling factor (0.25 = quarter-Kelly)
        max_position_pct: maximum fraction of bankroll per trade
        available_bankroll: current cash available
        model_confidence: [0, 1] how much to trust the probability estimate
    """
    p = win_probability
    q = 1 - p
    b = payoff_ratio

    if b <= 0 or p <= 0 or p >= 1:
        return KellyResult(0, 0, 0, "invalid_inputs")

    # Classic Kelly: f* = (b*p - q) / b
    f_star = (b * p - q) / b

    if f_star <= 0:
        return KellyResult(
            optimal_fraction=round(f_star, 6),
            capped_fraction=0,
            proposed_size_usd=0,
            sizing_reason="negative_kelly_no_trade",
        )

    # Apply fractional Kelly
    f_scaled = f_star * kelly_fraction

    # Apply confidence scaling
    f_confident = f_scaled * model_confidence

    # Cap at max position size
    f_capped = min(f_confident, max_position_pct)

    proposed_usd = f_capped * available_bankroll

    reason = "full_kelly" if f_capped == f_confident else "capped_at_max"

    return KellyResult(
        optimal_fraction=round(f_star, 6),
        capped_fraction=round(f_capped, 6),
        proposed_size_usd=round(proposed_usd, 2),
        sizing_reason=reason,
    )
