"""Kill switch: consecutive loss cooldown and hard stop rules."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.paper import PaperPosition

# Exit reasons that are NOT real trading losses — exclude from kill switch logic
# stale_data / position_cap_cleanup / demo_cleanup exits are infrastructure exits,
# not signal quality failures. Counting them as losses would falsely trigger the switch.
NON_TRADING_EXITS = {"stale_data", "position_cap_cleanup", "demo_cleanup", "market_resolved"}

# Minimum loss to count as a real loss (not just fee/slippage noise)
MIN_REAL_LOSS = -0.005   # $0.005 threshold — below this is just noise


@dataclass
class KillSwitchStatus:
    is_active: bool
    consecutive_losses: int
    cooldown_remaining: int
    reason: str | None


async def check_kill_switch(
    db: AsyncSession,
    consecutive_loss_limit: int = 15,
    daily_loss_stop_pct: float = 0.20,
    starting_balance: float = 900.0,
) -> KillSwitchStatus:
    """Check if the system should stop trading.

    Triggers when:
    1. N consecutive real trading losses (excludes infrastructure exits and noise)
    2. Daily PnL exceeds the loss stop threshold
    """
    result = await db.execute(
        select(PaperPosition)
        .where(PaperPosition.status == "closed")
        .order_by(PaperPosition.closed_at.desc())
        .limit(consecutive_loss_limit + 20)
    )
    recent = result.scalars().all()

    if not recent:
        return KillSwitchStatus(False, 0, 0, None)

    # Filter to only real trading exits (not infrastructure/cleanup exits)
    trading_exits = [
        p for p in recent
        if (p.exit_reason or "") not in NON_TRADING_EXITS
    ]

    # Count consecutive real losses (pnl < MIN_REAL_LOSS, not just noise)
    consecutive = 0
    for p in trading_exits:
        pnl = float(p.realized_pnl or 0)
        if pnl < MIN_REAL_LOSS:
            consecutive += 1
        else:
            break

    if consecutive >= consecutive_loss_limit:
        return KillSwitchStatus(
            is_active=True,
            consecutive_losses=consecutive,
            cooldown_remaining=consecutive - consecutive_loss_limit + 1,
            reason=f"consecutive_losses: {consecutive} >= {consecutive_loss_limit}",
        )

    # Daily loss check — use all exits including infrastructure ones for total P&L
    total_daily_pnl = sum(float(p.realized_pnl or 0) for p in recent)
    daily_loss_pct = abs(total_daily_pnl) / starting_balance if total_daily_pnl < 0 else 0

    if daily_loss_pct > daily_loss_stop_pct:
        return KillSwitchStatus(
            is_active=True,
            consecutive_losses=consecutive,
            cooldown_remaining=0,
            reason=f"daily_loss_stop: {daily_loss_pct:.1%} > {daily_loss_stop_pct:.0%}",
        )

    return KillSwitchStatus(
        is_active=False,
        consecutive_losses=consecutive,
        cooldown_remaining=0,
        reason=None,
    )
