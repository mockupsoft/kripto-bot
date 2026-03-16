"""
Market Inefficiency Heatmap.

Which market categories generate the most detectable edge?

Method:
  1. For each market, compute "price inefficiency score":
     - avg spread as % of price (wider = less efficient)
     - repricing speed (how quickly does market respond to related market moves)
     - wallet edge concentration (do smart wallets cluster here?)
     - volume-adjusted spread
  2. Aggregate by category
  3. Produce a heatmap showing where edge lives

This is critical for strategy calibration: you should focus
copy trading and dislocation strategies on the least efficient markets.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market import Market, MarketSnapshot
from app.models.trade import WalletTransaction
from app.models.wallet import WalletScore

logger = logging.getLogger(__name__)


async def compute_market_inefficiency(
    db: AsyncSession,
    market_id,
    lookback_hours: int = 24,
) -> dict[str, float]:
    """
    Compute inefficiency metrics for a single market.

    Returns scores where HIGHER = MORE inefficient = MORE edge opportunity.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    snaps_result = await db.execute(
        select(MarketSnapshot)
        .where(
            MarketSnapshot.market_id == market_id,
            MarketSnapshot.captured_at >= since,
        )
        .order_by(MarketSnapshot.captured_at.asc())
    )
    snaps = snaps_result.scalars().all()

    if len(snaps) < 3:
        return {"spread_score": 0, "volatility_score": 0, "composite": 0, "sample_count": 0}

    # 1. Average spread (higher spread = less efficient)
    spreads = [float(s.spread) for s in snaps if s.spread and s.spread > 0]
    avg_spread = sum(spreads) / len(spreads) if spreads else 0

    # Spread as fraction of midpoint price
    midpoints = [float(s.midpoint) for s in snaps if s.midpoint and s.midpoint > 0]
    avg_mid = sum(midpoints) / len(midpoints) if midpoints else 0.5
    relative_spread = avg_spread / avg_mid if avg_mid > 0 else 0

    # 2. Price volatility (more volatile = more opportunity but also more risk)
    if len(midpoints) > 2:
        diffs = [abs(midpoints[i + 1] - midpoints[i]) for i in range(len(midpoints) - 1)]
        price_vol = sum(diffs) / len(diffs)
    else:
        price_vol = 0

    # 3. Volume score: more volume = more efficient (lower score)
    volumes = [float(s.volume_24h) for s in snaps if s.volume_24h and s.volume_24h > 0]
    avg_vol = sum(volumes) / len(volumes) if volumes else 0
    # Normalize: 0 volume = high inefficiency, high volume = low inefficiency
    volume_inefficiency = 1.0 / (1.0 + avg_vol / 100_000)

    # 4. Spread stability (high variance in spread = less predictable = lower quality)
    if len(spreads) > 2:
        import statistics
        spread_std = statistics.stdev(spreads)
        spread_stability = 1.0 - min(spread_std / (avg_spread + 0.001), 1.0)
    else:
        spread_stability = 0.5

    # Composite inefficiency score
    # Higher weight to spread and volume as they are most reliable signals
    composite = (
        0.40 * min(relative_spread * 10, 1.0)   # Normalized relative spread
        + 0.30 * min(price_vol * 20, 1.0)         # Normalized price volatility
        + 0.20 * volume_inefficiency               # Volume-based
        + 0.10 * (1 - spread_stability)            # Spread instability
    )

    return {
        "avg_spread": round(avg_spread, 6),
        "relative_spread": round(relative_spread, 6),
        "price_volatility": round(price_vol, 6),
        "volume_24h": round(avg_vol, 2),
        "spread_score": round(min(relative_spread * 10, 1.0), 4),
        "volatility_score": round(min(price_vol * 20, 1.0), 4),
        "volume_inefficiency": round(volume_inefficiency, 4),
        "composite": round(composite, 4),
        "sample_count": len(snaps),
    }


async def build_inefficiency_heatmap(db: AsyncSession) -> dict[str, Any]:
    """
    Build the full market inefficiency heatmap.

    Returns per-category aggregation and per-market rankings.
    """
    # Get all active markets
    markets_result = await db.execute(
        select(Market).where(Market.is_active == True)  # noqa: E712
    )
    markets = markets_result.scalars().all()

    if not markets:
        return {"categories": [], "top_markets": [], "heatmap": {}}

    per_market = []
    category_data: dict[str, list[dict]] = defaultdict(list)

    for market in markets:
        try:
            metrics = await compute_market_inefficiency(db, market.id)
            if metrics["sample_count"] < 2:
                continue

            # Infer category from metadata or question
            category = _infer_category(market)

            entry = {
                "market_id": str(market.id),
                "question": (market.question or "")[:80],
                "category": category,
                "composite_score": metrics["composite"],
                "avg_spread": metrics["avg_spread"],
                "price_volatility": metrics["price_volatility"],
                "volume_24h": metrics["volume_24h"],
            }
            per_market.append(entry)
            category_data[category].append(metrics)
        except Exception as e:
            logger.debug(f"Inefficiency failed for market {market.id}: {e}")

    # Aggregate by category
    category_summary = []
    for cat, metrics_list in category_data.items():
        if not metrics_list:
            continue
        avg_composite = sum(m["composite"] for m in metrics_list) / len(metrics_list)
        avg_spread = sum(m["avg_spread"] for m in metrics_list) / len(metrics_list)
        avg_vol = sum(m["volume_24h"] for m in metrics_list) / len(metrics_list)
        category_summary.append({
            "category": cat,
            "market_count": len(metrics_list),
            "avg_inefficiency": round(avg_composite, 4),
            "avg_spread": round(avg_spread, 6),
            "avg_volume_24h": round(avg_vol, 2),
            "verdict": _category_verdict(avg_composite),
        })

    # Sort categories by inefficiency descending
    category_summary.sort(key=lambda x: x["avg_inefficiency"], reverse=True)

    # Top 20 most inefficient individual markets
    top_markets = sorted(per_market, key=lambda x: x["composite_score"], reverse=True)[:20]

    return {
        "categories": category_summary,
        "top_markets": top_markets,
        "total_markets_analysed": len(per_market),
        "insight": (
            "Higher inefficiency score = more edge opportunity. "
            "Focus copy trading on the top categories. "
            "Avoid low-inefficiency markets where spreads are tight and volume is high."
        ),
    }


def _infer_category(market: Market) -> str:
    """Infer market category from metadata or question text."""
    # Try metadata first
    meta = market.metadata_ or {}
    if isinstance(meta, dict):
        cat = meta.get("category") or ""
        if cat and cat not in ("general", ""):
            return cat.lower()

    # Fallback: keyword matching on question
    q = (market.question or "").lower()
    if any(k in q for k in ["btc", "bitcoin", "eth", "ethereum", "crypto", "sol", "xrp"]):
        return "crypto"
    if any(k in q for k in ["fed", "fomc", "rate", "inflation", "gdp", "economy"]):
        return "macro"
    if any(k in q for k in ["election", "vote", "president", "congress", "senate"]):
        return "politics"
    if any(k in q for k in ["war", "military", "strike", "attack", "israel", "iran", "ukraine"]):
        return "geopolitics"
    if any(k in q for k in ["nba", "nfl", "sport", "match", "game", "tournament"]):
        return "sports"
    if any(k in q for k in ["ai", "openai", "model", "gpt", "tech"]):
        return "tech"
    return "general"


def _category_verdict(avg_inefficiency: float) -> str:
    if avg_inefficiency > 0.5:
        return "HIGH_EDGE: significant pricing inefficiency, best for arbitrage"
    if avg_inefficiency > 0.3:
        return "MODERATE_EDGE: some inefficiency, worth monitoring"
    if avg_inefficiency > 0.15:
        return "LOW_EDGE: mostly efficient, narrow margins"
    return "EFFICIENT: tight spreads and high volume, avoid"
