"""
Alpha Persistence Analysis.

Key question: Is a wallet's edge real (strategy) or noise (luck)?

A wallet that was profitable in week 1 and also in week 2 has persistent alpha.
A wallet that was profitable in week 1 but random in week 2 was just lucky.

Method:
  1. Split wallet's trades into rolling time windows (e.g. weekly)
  2. Compute ROI/hit_rate per window
  3. Calculate autocorrelation of performance: does past performance predict future?
  4. Produce a persistence score and time-series chart

High persistence → real edge (strategy)
Low persistence → noise (luck), don't copy

This is the "alpha decay" test used by hedge funds to vet signal quality.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trade import WalletTransaction
from app.models.wallet import Wallet

logger = logging.getLogger(__name__)

WINDOW_DAYS = 7  # rolling weekly performance windows


async def compute_alpha_persistence(
    db: AsyncSession,
    wallet_id,
    window_days: int = WINDOW_DAYS,
) -> dict[str, Any]:
    """
    Compute the persistence of a wallet's alpha over time.

    Returns:
      - windows: list of time windows with per-period ROI
      - persistence_score: 0-1 (1 = perfectly persistent, 0 = random)
      - autocorrelation: lag-1 autocorrelation of per-period ROI
      - verdict: plain language explanation
    """
    tx_result = await db.execute(
        select(WalletTransaction)
        .where(WalletTransaction.wallet_id == wallet_id)
        .order_by(WalletTransaction.occurred_at.asc())
    )
    trades = tx_result.scalars().all()

    if len(trades) < 3:
        return _empty_persistence(wallet_id)

    # Group trades by time window
    windows = _bucket_trades(trades, window_days)
    if len(windows) < 1:
        return _empty_persistence(wallet_id)

    # Compute per-window metrics
    window_metrics = []
    for window_start, window_trades in sorted(windows.items()):
        prices = [float(t.price) for t in window_trades if t.price]
        notionals = [float(t.notional) for t in window_trades if t.notional]
        sides = [t.side for t in window_trades]

        if not prices:
            continue

        # Simple ROI proxy: directional price position vs neutral (0.5)
        roi_proxy = 0.0
        for i, (price, side) in enumerate(zip(prices, sides)):
            if side == "BUY":
                roi_proxy += 0.5 - price   # positive if bought below 50%
            else:
                roi_proxy += price - 0.5   # positive if sold above 50%

        total_notional = sum(notionals) if notionals else len(prices)
        roi_normalized = roi_proxy / max(total_notional, 1)

        hits = sum(
            1 for p, s in zip(prices, sides)
            if (s == "BUY" and p < 0.5) or (s == "SELL" and p > 0.5)
        )

        window_metrics.append({
            "window_start": window_start.isoformat(),
            "trade_count": len(window_trades),
            "roi_proxy": round(roi_normalized, 6),
            "hit_rate": round(hits / len(window_trades), 4),
            "avg_price": round(sum(prices) / len(prices), 4),
        })

    if len(window_metrics) < 2:
        return _empty_persistence(wallet_id)

    rois = [w["roi_proxy"] for w in window_metrics]

    # Lag-1 autocorrelation: cor(ROI[t], ROI[t-1])
    autocorr = _lag1_autocorrelation(rois)

    # Persistence score: transform autocorrelation to 0-1
    # autocorr of +1.0 = perfectly persistent, 0 = random, -1 = mean-reverting
    persistence_score = max(0.0, (autocorr + 1) / 2)

    # Trend: is performance improving, declining, or flat?
    if len(rois) >= 3:
        first_half = rois[:len(rois) // 2]
        second_half = rois[len(rois) // 2:]
        avg_first = sum(first_half) / len(first_half)
        avg_second = sum(second_half) / len(second_half)
        trend = avg_second - avg_first
    else:
        trend = 0.0

    return {
        "wallet_id": str(wallet_id),
        "window_days": window_days,
        "total_windows": len(window_metrics),
        "windows": window_metrics,
        "autocorrelation": round(autocorr, 4),
        "persistence_score": round(persistence_score, 4),
        "performance_trend": round(trend, 6),
        "verdict": _persistence_verdict(persistence_score, autocorr, trend),
    }


async def compute_all_persistence(db: AsyncSession) -> list[dict]:
    """Compute persistence for all tracked wallets."""
    result = await db.execute(
        select(Wallet).where(Wallet.is_tracked == True)  # noqa: E712
    )
    wallets = result.scalars().all()

    profiles = []
    for w in wallets:
        try:
            p = await compute_alpha_persistence(db, w.id)
            p["label"] = w.label
            p["address"] = w.address
            p["is_real"] = not w.address.startswith("0xdemo")
            profiles.append(p)
        except Exception as e:
            logger.debug(f"Persistence failed for {w.address[:10]}: {e}")

    return sorted(profiles, key=lambda x: x["persistence_score"], reverse=True)


def _bucket_trades(trades: list, window_days: int) -> dict[datetime, list]:
    """Group trades into rolling time windows."""
    if not trades:
        return {}

    buckets: dict[datetime, list] = defaultdict(list)
    for t in trades:
        if not t.occurred_at:
            continue
        # Floor to window boundary
        days_since_epoch = (t.occurred_at - datetime(1970, 1, 1, tzinfo=timezone.utc)).days
        window_num = days_since_epoch // window_days
        window_start = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(days=window_num * window_days)
        buckets[window_start].append(t)

    return dict(buckets)


def _lag1_autocorrelation(series: list[float]) -> float:
    """Pearson autocorrelation at lag-1."""
    if len(series) < 3:
        return 0.0

    x = series[:-1]
    y = series[1:]
    n = len(x)

    mean_x = sum(x) / n
    mean_y = sum(y) / n

    numerator = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
    denom_x = math.sqrt(sum((v - mean_x) ** 2 for v in x))
    denom_y = math.sqrt(sum((v - mean_y) ** 2 for v in y))

    if denom_x * denom_y == 0:
        return 0.0

    return numerator / (denom_x * denom_y)


def _persistence_verdict(persistence: float, autocorr: float, trend: float) -> str:
    if persistence > 0.7:
        if trend > 0:
            return "STRONG_PERSISTENT_AND_IMPROVING: real edge, getting better"
        return "STRONG_PERSISTENT: real edge, copy this wallet"
    if persistence > 0.55:
        if trend < -0.01:
            return "MODERATE_DECAYING: was persistent but edge is shrinking"
        return "MODERATE_PERSISTENT: likely real edge, worth tracking"
    if autocorr < -0.2:
        return "MEAN_REVERTING: performance alternates, unreliable"
    if persistence > 0.45:
        return "BORDERLINE: inconclusive, need more data"
    return "NOISE: no persistence, likely luck not strategy"


def _empty_persistence(wallet_id) -> dict:
    return {
        "wallet_id": str(wallet_id),
        "window_days": WINDOW_DAYS,
        "total_windows": 0,
        "windows": [],
        "autocorrelation": 0.0,
        "persistence_score": 0.0,
        "performance_trend": 0.0,
        "verdict": "INSUFFICIENT_DATA",
    }
