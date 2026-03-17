"""Polymarket-style fee implementation.

Polymarket fee per share: fee_rate * price * (1 - price)
Fee peaks near p=0.5 and drops near 0 or 1 — incentivizes trading at extremes.

IMPORTANT: callers pass fee_rate_bps (basis points). Convert with / 10000.
Example: fee_rate_bps=200 → fee_rate=0.02 (2%).
"""

from __future__ import annotations


def compute_fee(
    price: float,
    size: float,
    fees_enabled: bool = True,
    fee_rate: float = 0.02,
) -> float:
    """Compute the total fee for a trade at a given price and size.

    fee_rate: decimal fee rate (e.g. 0.02 for 2%).
    Returns fee in USD terms.
    """
    if not fees_enabled or price <= 0 or price >= 1:
        return 0.0

    fee_per_unit = fee_rate * price * (1 - price)
    return round(fee_per_unit * size, 6)


def effective_fee_rate(price: float, fee_rate: float = 0.02) -> float:
    """Effective fee rate as a fraction of notional at given price."""
    if price <= 0 or price >= 1:
        return 0.0
    return fee_rate * (1 - price)
