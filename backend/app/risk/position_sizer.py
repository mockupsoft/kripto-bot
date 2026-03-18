"""Position size computation per signal."""

from __future__ import annotations

from app.risk.kelly import kelly_size, KellyResult


def wallet_tier_multiplier(composite_score: float) -> float:
    """Scale position size by wallet quality tier.

    excellent (>= 0.60): 1.0x — full conviction
    high (>= 0.50):      0.8x
    good (>= 0.40):      0.5x
    fair (< 0.40):        0.25x — minimal exposure
    """
    if composite_score >= 0.60:
        return 1.0
    if composite_score >= 0.50:
        return 0.8
    if composite_score >= 0.40:
        return 0.5
    return 0.25


def compute_position_size(
    model_probability: float,
    model_confidence: float,
    market_price: float,
    available_bankroll: float,
    kelly_fraction: float = 0.25,
    max_position_pct: float = 0.015,
    confidence_factor: float = 1.0,
    side: str = "BUY",
    wallet_composite: float = 0.5,
) -> KellyResult:
    """Determine how much to allocate to a given opportunity.

    Uses Kelly criterion with wallet-tier-adjusted sizing:
    - kelly_fraction is scaled by confidence_factor AND wallet tier
    - Higher quality wallets get proportionally larger positions
    """
    if side == "SELL":
        win_prob = 1.0 - model_probability
        price = 1.0 - market_price
    else:
        win_prob = model_probability
        price = market_price

    payoff_ratio = (1 - price) / max(price, 0.01)

    tier_mult = wallet_tier_multiplier(wallet_composite)
    effective_kelly = kelly_fraction * max(0.0, min(1.0, confidence_factor)) * tier_mult

    return kelly_size(
        win_probability=win_prob,
        payoff_ratio=payoff_ratio,
        kelly_fraction=effective_kelly,
        max_position_pct=max_position_pct,
        available_bankroll=available_bankroll,
        model_confidence=model_confidence,
    )
