"""
Trade Timing Analysis — Information Edge Detection.

Core question: Does a wallet trade BEFORE the market moves, or after?

If a wallet consistently trades 2-5 seconds before a price jump,
that is a strong signal of information edge (not luck).

Method:
  1. For each wallet trade, find the market price at T=0 (trade time)
  2. Look at the next N snapshots of that market (T+5s, T+15s, T+30s, T+60s, T+120s)
  3. Compute price delta at each horizon
  4. Aggregate across all trades: avg price move in direction of trade
  5. If consistently positive → information edge

A random trader should show ~0 avg move.
An information trader shows positive avg move that decays over time.
The speed of decay tells us the edge window.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trade import WalletTransaction
from app.models.market import MarketSnapshot, Market
from app.models.wallet import Wallet, WalletScore

logger = logging.getLogger(__name__)

TIMING_HORIZONS_SECONDS = [5, 15, 30, 60, 120, 300]


async def compute_trade_timing_profile(
    db: AsyncSession,
    wallet_id,
    lookback_days: int = 30,
) -> dict[str, Any]:
    """
    Compute the trade timing profile for a wallet.

    Returns:
      - price_move_by_horizon: avg directional price move at each time horizon
      - information_score: 0-1, how consistently this wallet precedes price moves
      - edge_window_seconds: estimated seconds before edge is fully priced in
      - trade_count: number of trades analysed
    """
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    # Fetch wallet's trades with market info
    tx_result = await db.execute(
        select(WalletTransaction)
        .where(
            WalletTransaction.wallet_id == wallet_id,
            WalletTransaction.occurred_at >= since,
            WalletTransaction.market_id.isnot(None),
        )
        .order_by(WalletTransaction.occurred_at.asc())
    )
    trades = tx_result.scalars().all()

    if len(trades) < 2:
        return _empty_timing_profile(wallet_id)

    horizon_moves: dict[int, list[float]] = {h: [] for h in TIMING_HORIZONS_SECONDS}
    pre_trade_moves: list[float] = []

    for trade in trades:
        if not trade.occurred_at or not trade.market_id or not trade.price:
            continue

        trade_price = float(trade.price)
        trade_time = trade.occurred_at
        is_buy = trade.side == "BUY"

        # Price N seconds before the trade (to check if info already priced in)
        pre_snap = await _get_snapshot_near(db, trade.market_id, trade_time - timedelta(seconds=30))
        if pre_snap and pre_snap.midpoint:
            pre_move = float(pre_snap.midpoint) - trade_price
            # Directional: positive = price moved toward trade direction before trade
            pre_trade_moves.append(pre_move if is_buy else -pre_move)

        # Price at each horizon after the trade
        for horizon_s in TIMING_HORIZONS_SECONDS:
            snap_after = await _get_snapshot_near(
                db, trade.market_id, trade_time + timedelta(seconds=horizon_s)
            )
            if snap_after and snap_after.midpoint:
                post_price = float(snap_after.midpoint)
                # Directional delta: positive = price moved in direction of trade
                delta = post_price - trade_price
                directional_delta = delta if is_buy else -delta
                horizon_moves[horizon_s].append(directional_delta)

    # Build profile
    price_move_by_horizon = {}
    for h, moves in horizon_moves.items():
        if moves:
            price_move_by_horizon[f"{h}s"] = {
                "avg_move": round(sum(moves) / len(moves), 6),
                "pct_positive": round(sum(1 for m in moves if m > 0.001) / len(moves), 4),
                "sample_count": len(moves),
            }

    # Information score: weighted average of pct_positive across horizons
    # Short horizons get higher weight (information decays)
    weights = {5: 3.0, 15: 2.5, 30: 2.0, 60: 1.5, 120: 1.0, 300: 0.5}
    weighted_sum = 0.0
    weight_total = 0.0
    for h, data in price_move_by_horizon.items():
        h_int = int(h.replace("s", ""))
        w = weights.get(h_int, 1.0)
        weighted_sum += data["pct_positive"] * w
        weight_total += w

    information_score = weighted_sum / weight_total if weight_total > 0 else 0.0
    # Normalize: 0.5 is random, scale to 0-1 where 1.0 = always precedes move
    information_score = max(0.0, min(1.0, (information_score - 0.5) * 2))

    # Edge window: find the last horizon where avg_move is still positive
    edge_window_s = 0
    for h in sorted(TIMING_HORIZONS_SECONDS):
        key = f"{h}s"
        if key in price_move_by_horizon and price_move_by_horizon[key]["avg_move"] > 0.002:
            edge_window_s = h

    # Pre-trade signal: did price already move before trade? (info already in)
    avg_pre_move = sum(pre_trade_moves) / len(pre_trade_moves) if pre_trade_moves else 0.0

    return {
        "wallet_id": str(wallet_id),
        "trade_count": len(trades),
        "price_move_by_horizon": price_move_by_horizon,
        "information_score": round(information_score, 4),
        "edge_window_seconds": edge_window_s,
        "avg_pre_trade_drift": round(avg_pre_move, 6),
        "verdict": _timing_verdict(information_score, edge_window_s, avg_pre_move),
    }


async def compute_all_wallet_timing(db: AsyncSession) -> list[dict]:
    """Compute timing profiles for all tracked real wallets."""
    result = await db.execute(
        select(Wallet).where(
            Wallet.is_tracked == True,  # noqa: E712
            ~Wallet.address.like("0xdemo%"),
        )
    )
    wallets = result.scalars().all()

    profiles = []
    for w in wallets:
        try:
            profile = await compute_trade_timing_profile(db, w.id)
            profile["label"] = w.label
            profile["address"] = w.address
            profiles.append(profile)
        except Exception as e:
            logger.debug(f"Timing profile failed for {w.address[:10]}: {e}")

    # Sort by information score descending
    return sorted(profiles, key=lambda x: x["information_score"], reverse=True)


async def _get_snapshot_near(
    db: AsyncSession,
    market_id,
    target_time: datetime,
    window_seconds: int = 120,
) -> MarketSnapshot | None:
    """Find the closest snapshot to target_time within a time window."""
    # Look for snapshots within window around target time
    result = await db.execute(
        select(MarketSnapshot)
        .where(
            and_(
                MarketSnapshot.market_id == market_id,
                MarketSnapshot.captured_at >= target_time - timedelta(seconds=window_seconds),
                MarketSnapshot.captured_at <= target_time + timedelta(seconds=window_seconds),
            )
        )
        .order_by(
            func.abs(
                func.extract("epoch", MarketSnapshot.captured_at)
                - func.extract("epoch", target_time)
            )
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


def _timing_verdict(info_score: float, edge_window_s: int, pre_move: float) -> str:
    if pre_move > 0.01:
        return "LAGGING: trades after price has already moved — follower, not leader"
    if info_score > 0.6 and edge_window_s >= 30:
        return "INFORMATION_EDGE: consistently precedes price moves, wide edge window"
    if info_score > 0.4 and edge_window_s >= 15:
        return "POSSIBLE_EDGE: some evidence of leading price moves"
    if info_score > 0.3:
        return "WEAK_EDGE: slight tendency to lead moves, could be noise"
    return "NO_EDGE: no detectable timing advantage over random"


def _empty_timing_profile(wallet_id) -> dict:
    return {
        "wallet_id": str(wallet_id),
        "trade_count": 0,
        "price_move_by_horizon": {},
        "information_score": 0.0,
        "edge_window_seconds": 0,
        "avg_pre_trade_drift": 0.0,
        "verdict": "INSUFFICIENT_DATA",
    }
