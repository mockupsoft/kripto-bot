"""Polymarket fee formula implementation.

Fee = C * p * feeRate * (p * (1-p))^exponent

For crypto 5-minute markets: feeRate=0.25, exponent=2.
Fee peaks near p=0.5 and drops near 0 or 1.
"""

from __future__ import annotations


def compute_fee(
    price: float,
    size: float,
    fees_enabled: bool = True,
    fee_rate: float = 0.25,
    exponent: float = 2.0,
) -> float:
    """Compute the total fee for a trade at a given price and size.

    Returns fee in USD terms.
    """
    if not fees_enabled or price <= 0 or price >= 1:
        return 0.0

    C = 2.0
    fee_per_unit = C * price * fee_rate * (price * (1 - price)) ** exponent
    return round(fee_per_unit * size, 6)


def effective_fee_rate(price: float, fee_rate: float = 0.25, exponent: float = 2.0) -> float:
    """Effective fee rate as a fraction of notional value at given price."""
    if price <= 0 or price >= 1:
        return 0.0
    C = 2.0
    return C * fee_rate * (price * (1 - price)) ** exponent
