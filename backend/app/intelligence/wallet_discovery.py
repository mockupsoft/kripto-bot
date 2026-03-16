"""
Wallet auto-discovery engine.

Watches the Polymarket public trades stream to automatically detect and
onboard high-activity traders. No manual wallet addition needed.

Strategy:
  1. Pull recent trades from /trades endpoint
  2. Aggregate by wallet address: volume, frequency, market diversity
  3. Score candidate wallets
  4. Auto-add wallets above the threshold for tracking
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from app.dependencies import async_session_factory
from app.models.wallet import Wallet

logger = logging.getLogger(__name__)

DATA_API = "https://data-api.polymarket.com"
DISCOVERY_THRESHOLD_VOLUME = 50.0   # min total notional to be worth tracking
DISCOVERY_THRESHOLD_TRADES = 3      # min trade count in discovery window
MAX_AUTO_WALLETS = 300              # cap to avoid DB bloat


async def fetch_recent_trades(limit: int = 100) -> list[dict]:
    """Pull the latest trades across all Polymarket markets."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(f"{DATA_API}/trades", params={"limit": limit})
            if resp.status_code == 200:
                return resp.json() or []
        except Exception as e:
            logger.warning(f"Trade stream fetch failed: {e}")
    return []


async def discover_wallets_from_trades(trades: list[dict]) -> list[dict]:
    """
    Analyse a batch of trades and rank wallet candidates.

    Returns a list of dicts sorted by score descending.
    """
    wallet_stats: dict[str, dict] = defaultdict(lambda: {
        "address": "",
        "trade_count": 0,
        "total_volume": 0.0,
        "markets": set(),
        "avg_price": [],
        "sides": [],
    })

    for t in trades:
        addr = (t.get("proxyWallet") or "").lower()
        if not addr or len(addr) != 42:
            continue

        stats = wallet_stats[addr]
        stats["address"] = addr
        stats["trade_count"] += 1
        stats["total_volume"] += float(t.get("usdcSize") or t.get("size") or 0)
        stats["markets"].add(t.get("conditionId") or t.get("asset") or "")
        if t.get("price"):
            stats["avg_price"].append(float(t["price"]))
        if t.get("side"):
            stats["sides"].append(t["side"].upper())

    candidates = []
    for addr, stats in wallet_stats.items():
        if (
            stats["trade_count"] >= DISCOVERY_THRESHOLD_TRADES
            and stats["total_volume"] >= DISCOVERY_THRESHOLD_VOLUME
        ):
            avg_p = sum(stats["avg_price"]) / len(stats["avg_price"]) if stats["avg_price"] else 0.5
            market_div = len(stats["markets"])
            # Simple discovery score: volume * diversity / (1 + concentration_penalty)
            score = (stats["total_volume"] * market_div) / max(stats["trade_count"], 1)
            candidates.append({
                "address": addr,
                "trade_count": stats["trade_count"],
                "total_volume": round(stats["total_volume"], 2),
                "market_count": market_div,
                "avg_price": round(avg_p, 4),
                "discovery_score": round(score, 2),
            })

    return sorted(candidates, key=lambda x: x["discovery_score"], reverse=True)


async def run_discovery_cycle() -> dict:
    """
    Full discovery cycle:
    1. Fetch recent trade stream
    2. Identify candidate wallets
    3. Auto-add new ones to DB
    Returns summary stats.
    """
    trades = await fetch_recent_trades(limit=200)
    if not trades:
        return {"discovered": 0, "already_tracked": 0, "skipped": 0}

    candidates = await discover_wallets_from_trades(trades)
    logger.info(f"Discovery: {len(candidates)} candidates from {len(trades)} trades")

    discovered = already_tracked = skipped = 0

    async with async_session_factory() as db:
        # Check current tracked count
        total_result = await db.scalar(
            select(__import__("sqlalchemy", fromlist=["func"]).func.count(Wallet.id))
        )
        if (total_result or 0) >= MAX_AUTO_WALLETS:
            logger.info(f"Discovery: at cap ({MAX_AUTO_WALLETS}), skipping")
            return {"discovered": 0, "already_tracked": total_result, "skipped": len(candidates)}

        for c in candidates:
            addr = c["address"]
            existing = await db.execute(select(Wallet).where(Wallet.address == addr))
            wallet = existing.scalar_one_or_none()

            if wallet:
                if not wallet.is_tracked:
                    wallet.is_tracked = True
                    already_tracked += 1
                continue

            wallet = Wallet(
                address=addr,
                label=f"{addr[:8]}...{addr[-4:]}",
                is_tracked=True,
                metadata={
                    "source": "auto_discovery",
                    "discovery_score": c["discovery_score"],
                    "trade_count_at_discovery": c["trade_count"],
                    "volume_at_discovery": c["total_volume"],
                    "discovered_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            db.add(wallet)
            discovered += 1

        await db.commit()

    logger.info(f"Discovery: +{discovered} new, {already_tracked} updated, {skipped} skipped")
    return {"discovered": discovered, "already_tracked": already_tracked, "skipped": skipped}


async def fetch_and_ingest_wallet_trades_bulk(addresses: list[str]) -> int:
    """
    Batch-fetch trade history for a list of wallet addresses.
    More efficient than calling ingest_wallet_activity individually.
    """
    from app.ingestion.live_ingestion import ingest_wallet_activity

    total = 0
    for addr in addresses:
        try:
            count = await ingest_wallet_activity(addr)
            total += count
            await asyncio.sleep(0.3)  # rate limit compliance
        except Exception as e:
            logger.warning(f"Bulk ingest failed for {addr}: {e}")
    return total
