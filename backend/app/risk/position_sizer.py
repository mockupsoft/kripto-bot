"""Position size computation per signal."""

from __future__ import annotations

from app.risk.kelly import kelly_size, KellyResult


def compute_position_size(
    model_probability: float,
    model_confidence: float,
    market_price: float,
    available_bankroll: float,
    kelly_fraction: float = 0.25,
    max_position_pct: float = 0.08,
    confidence_factor: float = 1.0,
    side: str = "BUY",
) -> KellyResult:
    """Determine how much to allocate to a given opportunity.

    Uses Kelly criterion with the model's probability estimate and
    caps at the configured maximum position size.

    confidence_factor: [0, 1] composite haircut from staleness + slippage + depth.
    Multiplied into kelly_fraction so sizing shrinks when market conditions are poor.
    effective_kelly = kelly_fraction × confidence_factor

    For SELL trades (shorting YES side), probability is flipped so Kelly stays valid.
    """
    # For SELL, we're betting that the outcome is NO (prob of NO = 1 - market_price)
    # Adjust: treat it as buying NO at (1 - market_price)
    if side == "SELL":
        win_prob = 1.0 - model_probability
        price = 1.0 - market_price
    else:
        win_prob = model_probability
        price = market_price

    payoff_ratio = (1 - price) / max(price, 0.01)

    # Kelly × confidence_factor: less confident = smaller allocation
    effective_kelly = kelly_fraction * max(0.0, min(1.0, confidence_factor))

    return kelly_size(
        win_probability=win_prob,
        payoff_ratio=payoff_ratio,
        kelly_fraction=effective_kelly,
        max_position_pct=max_position_pct,
        available_bankroll=available_bankroll,
        model_confidence=model_confidence,
    )
