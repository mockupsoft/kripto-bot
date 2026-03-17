"""Configurable slippage simulation.

Provides both flat-model and book-walking-aware slippage estimation.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass
class SlippageResult:
    slippage_bps: float
    slippage_price: float
    adjusted_price: float


def estimate_slippage(
    base_price: float,
    side: str,
    base_slippage_bps: int = 5,
    volatility_multiplier: float = 0.5,
    depth_factor: float = 0.3,
    current_volatility: float = 0.05,
    available_depth: float = 100.0,
    requested_size: float = 50.0,
    snapshot_age_ms: float = 0.0,
    rng: random.Random | None = None,
) -> SlippageResult:
    """Estimate slippage for a simulated trade.

    snapshot_age_ms: milliseconds since the book snapshot was taken.
    Older snapshots incur a staleness haircut — the book may have moved.
    This is the fallback when book_levels data is unavailable.
    When book data exists, book_walker.walk_book is preferred.
    """
    rng = rng or random.Random()

    # Size impact: larger orders relative to depth get worse fills
    size_ratio = requested_size / max(available_depth, 1)
    size_impact = size_ratio * depth_factor * 100  # in bps

    # Volatility impact: higher vol = wider effective spread
    vol_impact = current_volatility * volatility_multiplier * 100  # in bps

    # Staleness haircut: each 100ms of stale snapshot adds ~2 bps of extra slippage
    # At 1000ms stale: +20 bps. At 2000ms: +40 bps. Capped at 80 bps.
    staleness_bps = min(snapshot_age_ms / 100 * 2.0, 80.0)

    total_bps = base_slippage_bps + size_impact + vol_impact + staleness_bps

    # Add random noise (± 30%)
    noise = rng.uniform(0.7, 1.3)
    total_bps *= noise

    slippage_price = base_price * total_bps / 10000

    if side == "BUY":
        adjusted = base_price + slippage_price
    else:
        adjusted = base_price - slippage_price

    return SlippageResult(
        slippage_bps=round(total_bps, 2),
        slippage_price=round(slippage_price, 6),
        adjusted_price=round(adjusted, 6),
    )
