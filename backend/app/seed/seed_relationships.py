"""Seed market relationship pairs for spread detection."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market import Market, MarketRelationship


async def seed_relationships(db: AsyncSession) -> list[MarketRelationship]:
    result = await db.execute(select(Market).where(Market.is_active.is_(True)))
    markets = result.scalars().all()

    by_slug: dict[str, Market] = {m.slug: m for m in markets if m.slug}

    pairs = [
        ("btc-up-5m-window-1", "btc-up-15m-window-1", "same_asset_diff_window", "BTC"),
        ("btc-up-5m-window-1", "btc-up-5m-window-2", "adjacent_window", "BTC"),
        ("btc-up-15m-window-1", "btc-up-15m-window-2", "adjacent_window", "BTC"),
        ("eth-up-5m-window-1", "eth-up-15m-window-1", "same_asset_diff_window", "ETH"),
        ("eth-up-5m-window-1", "eth-up-5m-window-2", "adjacent_window", "ETH"),
        ("sol-up-5m-window-1", "sol-up-15m-window-1", "same_asset_diff_window", "SOL"),
        ("xrp-up-5m-window-1", "xrp-up-15m-window-1", "same_asset_diff_window", "XRP"),
        ("btc-up-5m-window-1", "eth-up-5m-window-1", "correlated", "BTC"),
    ]

    relationships = []
    for slug_a, slug_b, rel_type, asset in pairs:
        ma = by_slug.get(slug_a)
        mb = by_slug.get(slug_b)
        if not ma or not mb:
            continue
        r = MarketRelationship(
            market_a_id=ma.id,
            market_b_id=mb.id,
            relationship_type=rel_type,
            asset_symbol=asset,
            description=f"{slug_a} <-> {slug_b}",
            normal_spread_mean=0.02,
            normal_spread_std=0.015,
            is_active=True,
        )
        db.add(r)
        relationships.append(r)

    await db.flush()
    return relationships
