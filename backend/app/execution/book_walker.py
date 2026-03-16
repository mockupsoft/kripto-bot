"""Order book walker: simulates fills by walking through book levels.

This is where "demo vs fantasy" is decided. Instead of flat slippage,
we walk actual order book levels from market_snapshots.book_levels.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BookWalkResult:
    filled_size: float
    unfilled_size: float
    wavg_fill_price: float
    levels_consumed: int
    total_cost: float
    is_partial: bool


def walk_book(
    book_levels: dict,
    side: str,
    requested_size: float,
    staleness_haircut: float = 0.0,
) -> BookWalkResult:
    """Walk through order book levels to simulate a realistic fill.

    Args:
        book_levels: {"bids": [{"p":"0.48","s":"30"}, ...], "asks": [...]}
        side: "BUY" (walk asks) or "SELL" (walk bids)
        requested_size: total size to fill
        staleness_haircut: [0, 1] reduce available depth by this fraction to account
                           for the book potentially having changed since snapshot

    Returns:
        BookWalkResult with filled size, WAVG price, levels consumed, etc.
    """
    if side == "BUY":
        levels = book_levels.get("asks", [])
    else:
        levels = book_levels.get("bids", [])

    if not levels:
        return BookWalkResult(
            filled_size=0,
            unfilled_size=requested_size,
            wavg_fill_price=0,
            levels_consumed=0,
            total_cost=0,
            is_partial=True,
        )

    remaining = requested_size
    total_value = 0.0
    total_filled = 0.0
    levels_consumed = 0

    for level in levels:
        price = float(level.get("p", 0))
        available = float(level.get("s", 0))

        if staleness_haircut > 0:
            available *= (1 - staleness_haircut)

        if available <= 0:
            continue

        fill_at_level = min(remaining, available)
        total_value += fill_at_level * price
        total_filled += fill_at_level
        remaining -= fill_at_level
        levels_consumed += 1

        if remaining <= 0:
            break

    wavg_price = total_value / total_filled if total_filled > 0 else 0

    return BookWalkResult(
        filled_size=round(total_filled, 6),
        unfilled_size=round(remaining, 6),
        wavg_fill_price=round(wavg_price, 6),
        levels_consumed=levels_consumed,
        total_cost=round(total_value, 4),
        is_partial=remaining > 0,
    )
