"""Position size computation per signal."""

from __future__ import annotations

from app.risk.kelly import kelly_size, KellyResult


def tier_params(composite_score: float) -> tuple[str, float, float]:
    """Returns (tier, kelly_multiplier, max_position_pct) by wallet quality.

    A-tier: strong wallet → bigger positions, more capital
    B-tier: standard
    C-tier: weak → small positions, tight risk
    """
    if composite_score >= 0.55:
        return "A", 1.5, 0.04      # 1.5x kelly, 4% cap
    if composite_score >= 0.40:
        return "B", 1.0, 0.025     # standard, 2.5% cap
    return "C", 0.5, 0.01          # half kelly, 1% cap


def wallet_tier_multiplier(composite_score: float) -> float:
    """Backwards-compatible multiplier (used by some callers)."""
    _, mult, _ = tier_params(composite_score)
    return mult


def compute_position_size(
    model_probability: float,
    model_confidence: float,
    market_price: float,
    available_bankroll: float,
    kelly_fraction: float = 0.25,
    max_position_pct: float = 0.025,
    confidence_factor: float = 1.0,
    side: str = "BUY",
    wallet_composite: float = 0.5,
) -> KellyResult:
    """Determine how much to allocate to a given opportunity.

    Tier-aware sizing: A-tier gets 1.5x kelly + 4% cap,
    C-tier gets 0.5x kelly + 1% cap. This means A-tier positions
    are ~3x larger than C-tier on the same signal.
    """
    if side == "SELL":
        win_prob = 1.0 - model_probability
        price = 1.0 - market_price
    else:
        win_prob = model_probability
        price = market_price

    payoff_ratio = (1 - price) / max(price, 0.01)

    tier, tier_mult, tier_max_pct = tier_params(wallet_composite)
    effective_kelly = kelly_fraction * max(0.0, min(1.0, confidence_factor)) * tier_mult
    effective_max_pct = tier_max_pct

    return kelly_size(
        win_probability=win_prob,
        payoff_ratio=payoff_ratio,
        kelly_fraction=effective_kelly,
        max_position_pct=effective_max_pct,
        available_bankroll=available_bankroll,
        model_confidence=model_confidence,
    )
