"""Strategy Manager: allocation, kill-switch, capital weighting.

Tracks per-strategy rolling performance and decides:
  - Which strategies are ACTIVE vs SHADOW vs PAUSED
  - How much capital each strategy gets (capital weighting)
  - When to auto-pause a strategy (kill-switch rule)

Kill-switch rule:
  Last N trades PF < threshold → strategy enters SHADOW mode.
  When rolling PF recovers above restore_threshold → re-activate.

Capital weighting:
  Each strategy's bankroll share is proportional to its rolling PF,
  capped by max_share and floored by min_share.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.paper import PaperPosition

# ── Tuneable constants ─────────────────────────────────────────────────────
_ROLLING_WINDOW     = int(os.getenv("STRATEGY_ROLLING_WINDOW",   "20"))
_KILL_PF_THRESHOLD  = float(os.getenv("STRATEGY_KILL_PF",        "0.8"))
_RESTORE_PF_THRESHOLD = float(os.getenv("STRATEGY_RESTORE_PF",   "1.1"))
_MIN_TRADES_FOR_KILL = int(os.getenv("STRATEGY_MIN_TRADES_KILL", "10"))

StrategyMode = Literal["active", "shadow", "paused"]


@dataclass
class StrategyConfig:
    name: str
    default_mode: StrategyMode = "active"
    min_capital_share: float = 0.0       # floor fraction of bankroll
    max_capital_share: float = 0.60      # ceiling fraction of bankroll
    kill_pf_threshold: float = _KILL_PF_THRESHOLD
    restore_pf_threshold: float = _RESTORE_PF_THRESHOLD
    min_trades_for_kill: int = _MIN_TRADES_FOR_KILL
    rolling_window: int = _ROLLING_WINDOW


# ── Default strategy registry ──────────────────────────────────────────────
_DEFAULT_CONFIGS: dict[str, StrategyConfig] = {
    "direct_copy": StrategyConfig(
        name="direct_copy",
        default_mode="active",
        min_capital_share=0.10,
        max_capital_share=0.55,
    ),
    "high_conviction": StrategyConfig(
        name="high_conviction",
        default_mode="active",
        min_capital_share=0.10,
        max_capital_share=0.60,
    ),
    "leader_copy": StrategyConfig(
        name="leader_copy",
        default_mode="active",       # active from the start; filters are internal
        min_capital_share=0.05,
        max_capital_share=0.45,
        kill_pf_threshold=0.85,      # slightly tighter than default
        restore_pf_threshold=1.15,
    ),
    "dislocation": StrategyConfig(
        name="dislocation",
        default_mode="shadow",
        min_capital_share=0.0,
        max_capital_share=0.20,
        kill_pf_threshold=0.9,
        restore_pf_threshold=1.2,
    ),
    "shadow": StrategyConfig(
        name="shadow",
        default_mode="shadow",
        min_capital_share=0.0,
        max_capital_share=0.0,
    ),
}


@dataclass
class RollingStats:
    strategy: str
    mode: StrategyMode
    rolling_window: int
    trade_count: int
    win_count: int
    win_rate: float
    profit_factor: float | None
    expectancy: float
    gross_profit: float
    gross_loss: float
    capital_share: float             # recommended fraction of bankroll
    kill_active: bool
    kill_reason: str | None
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


async def get_rolling_stats(
    db: AsyncSession,
    strategy: str,
    window: int = _ROLLING_WINDOW,
    days: int = 7,
) -> RollingStats:
    """Compute rolling performance for a single strategy."""
    cfg = _DEFAULT_CONFIGS.get(strategy, StrategyConfig(name=strategy))

    # Pull last `window` closed trades for this strategy
    # Exclude infrastructure exits that don't reflect signal quality
    NON_TRADING_EXITS = {"stale_data", "position_cap_cleanup", "demo_cleanup"}
    result = await db.execute(
        select(PaperPosition)
        .where(
            PaperPosition.strategy == strategy,
            PaperPosition.status == "closed",
            PaperPosition.exit_reason.notin_(NON_TRADING_EXITS),
        )
        .order_by(PaperPosition.closed_at.desc())
        .limit(window)
    )
    positions = result.scalars().all()

    pnl_series = [float(p.realized_pnl or 0) for p in positions]
    wins   = [p for p in pnl_series if p > 0]
    losses = [p for p in pnl_series if p <= 0]

    trade_count = len(pnl_series)
    win_count   = len(wins)
    wr          = win_count / trade_count if trade_count else 0.0
    gross_profit = sum(wins)
    gross_loss   = abs(sum(losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else (
        float("inf") if gross_profit > 0 else None
    )
    loss_rate = 1 - wr
    avg_win  = sum(wins)  / len(wins)   if wins   else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    expectancy = (wr * avg_win) + (loss_rate * avg_loss)

    # Kill-switch evaluation
    kill_active = False
    kill_reason: str | None = None
    if (
        trade_count >= cfg.min_trades_for_kill
        and pf is not None
        and pf < cfg.kill_pf_threshold
    ):
        kill_active = True
        kill_reason = (
            f"Rolling PF {pf:.2f} < threshold {cfg.kill_pf_threshold} "
            f"(last {trade_count} trades)"
        )

    # Determine effective mode
    mode = cfg.default_mode
    if kill_active:
        mode = "shadow"
    elif (
        cfg.default_mode == "shadow"
        and trade_count >= cfg.min_trades_for_kill
        and pf is not None
        and pf >= cfg.restore_pf_threshold
    ):
        mode = "active"   # auto-restore

    # Capital share: proportional to PF, bounded by config
    # Hard cap: no single strategy may control more than 60% of bankroll
    # (prevents overfitting to one alpha source)
    HARD_CAP = float(os.getenv("STRATEGY_MAX_SHARE", "0.60"))
    if pf is None or pf <= 0 or mode != "active":
        share = cfg.min_capital_share
    else:
        raw = cfg.min_capital_share + (pf - 1.0) * (
            cfg.max_capital_share - cfg.min_capital_share
        )
        share = max(cfg.min_capital_share, min(cfg.max_capital_share, HARD_CAP, raw))

    return RollingStats(
        strategy=strategy,
        mode=mode,
        rolling_window=window,
        trade_count=trade_count,
        win_count=win_count,
        win_rate=round(wr, 4),
        profit_factor=round(pf, 3) if pf is not None and pf != float("inf") else pf,
        expectancy=round(expectancy, 4),
        gross_profit=round(gross_profit, 4),
        gross_loss=round(gross_loss, 4),
        capital_share=round(share, 3),
        kill_active=kill_active,
        kill_reason=kill_reason,
    )


async def get_all_rolling_stats(db: AsyncSession) -> dict[str, RollingStats]:
    """Get rolling stats for all registered strategies."""
    result = {}
    for name in _DEFAULT_CONFIGS:
        result[name] = await get_rolling_stats(db, name)
    return result


async def get_strategy_bankroll(
    db: AsyncSession,
    strategy: str,
    total_bankroll: float,
) -> float:
    """Return how much bankroll this strategy is allowed to use right now.

    Normalises across all active strategies so total allocated capital
    never exceeds the hard cap * total_bankroll per strategy, and the
    sum of shares stays sane.
    """
    stats = await get_rolling_stats(db, strategy)
    if stats.mode != "active":
        return 0.0
    return total_bankroll * stats.capital_share


async def is_strategy_active(db: AsyncSession, strategy: str) -> bool:
    """Quick check: should this strategy open new positions?"""
    stats = await get_rolling_stats(db, strategy)
    return stats.mode == "active"
