"""Wallet copy alpha computation: compare copy results vs baselines."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AlphaResult:
    raw_alpha: float
    alpha_vs_random: float
    alpha_vs_delayed: float
    copyable_alpha: float


def compute_wallet_alpha(
    copy_pnls: list[float],
    random_baseline_pnl: float = 0.0,
    delayed_baseline_pnl: float = 0.0,
) -> AlphaResult:
    """Compute how much value the copy strategy adds vs baselines.

    Args:
        copy_pnls: PnL per copied trade
        random_baseline_pnl: expected PnL from random trades
        delayed_baseline_pnl: expected PnL from copying with extra 500ms delay
    """
    total = sum(copy_pnls)
    alpha_random = total - random_baseline_pnl
    alpha_delayed = total - delayed_baseline_pnl

    # Copyable alpha: only count if positive after all costs
    copyable = max(0, total)

    return AlphaResult(
        raw_alpha=round(total, 4),
        alpha_vs_random=round(alpha_random, 4),
        alpha_vs_delayed=round(alpha_delayed, 4),
        copyable_alpha=round(copyable, 4),
    )
