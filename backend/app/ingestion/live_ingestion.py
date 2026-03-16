"""
Live Polymarket data ingestion service.

Pulls real markets and real trade activity from public Polymarket APIs.
Stores everything in raw_events first, then normalizes into domain tables.
DEMO_MODE_ONLY: never places any real orders.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import get_settings
from app.dependencies import async_session_factory
from app.ingestion.event_store import store_raw_event
from app.ingestion.event_normalizer import process_pending_events
from app.models.market import Market, MarketSnapshot
from app.models.wallet import Wallet
from app.models.trade import WalletTransaction

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

# Rate limits: Polymarket allows ~10 req/s on Gamma, be conservative
REQUEST_DELAY = 0.5  # seconds between requests


async def fetch_live_markets(limit: int = 50) -> list[dict]:
    """Fetch currently active markets sorted by 24h volume."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        params = {
            "limit": limit,
            "closed": "false",
            "archived": "false",
            "active": "true",
            "order": "volume24hr",
            "ascending": "false",
        }
        resp = await client.get(f"{GAMMA_API}/markets", params=params)
        resp.raise_for_status()
        data = resp.json()
        # Gamma returns list or dict with value key
        if isinstance(data, list):
            return data
        return data.get("value", data) if isinstance(data, dict) else []


async def fetch_market_orderbook(token_id: str) -> dict | None:
    """Fetch CLOB order book for a specific token."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(f"{CLOB_API}/book", params={"token_id": token_id})
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.debug(f"Order book fetch failed for {token_id}: {e}")
    return None


async def fetch_wallet_trades(address: str, limit: int = 20) -> list[dict]:
    """Fetch recent trades for a wallet from the Data API."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                f"{DATA_API}/activity",
                params={"user": address, "limit": limit},
            )
            if resp.status_code == 200:
                return resp.json() or []
        except Exception as e:
            logger.debug(f"Wallet activity fetch failed for {address}: {e}")
    return []


async def fetch_market_trades(condition_id: str, limit: int = 20) -> list[dict]:
    """Fetch recent trades for a specific market."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                f"{DATA_API}/activity",
                params={"conditionId": condition_id, "limit": limit},
            )
            if resp.status_code == 200:
                return resp.json() or []
        except Exception as e:
            logger.debug(f"Market trades fetch failed for {condition_id}: {e}")
    return []


async def ingest_live_markets(limit: int = 200) -> int:
    """
    Pull top active markets from Polymarket and store them.
    Returns number of markets processed.
    """
    logger.info("Ingesting live markets from Polymarket Gamma API...")
    markets = await fetch_live_markets(limit=limit)
    
    async with async_session_factory() as db:
        count = 0
        for m in markets:
            try:
                # Store raw event first (raw-first pipeline)
                await store_raw_event(
                    db=db,
                    source="polymarket_gamma",
                    event_type="market_update",
                    payload=m,
                    source_timestamp=datetime.now(timezone.utc),
                )

                # Upsert into markets table
                from sqlalchemy import select
                existing = await db.execute(
                    select(Market).where(Market.polymarket_id == str(m.get("id", "")))
                )
                market = existing.scalar_one_or_none()

                prices = []
                try:
                    prices = [float(p) for p in (m.get("outcomePrices") or "[]").strip("[]").split(",") if p.strip()]
                except Exception:
                    prices = []

                token_ids = []
                try:
                    import json
                    token_ids = json.loads(m.get("clobTokenIds") or "[]")
                except Exception:
                    token_ids = []

                if not market:
                    market = Market(
                        polymarket_id=str(m.get("id", "")),
                        condition_id=m.get("conditionId", ""),
                        question=m.get("question", ""),
                        slug=m.get("slug", ""),
                        outcomes=m.get("outcomes", '["Yes","No"]'),
                        token_ids={"yes": token_ids[0] if len(token_ids) > 0 else "", "no": token_ids[1] if len(token_ids) > 1 else ""},
                        category=m.get("category", "general"),
                        is_active=not m.get("closed", False),
                        end_date=datetime.fromisoformat(m["endDate"].replace("Z", "+00:00")) if m.get("endDate") else None,
                        fees_enabled=bool(m.get("feesEnabled", False)),
                        fee_rate_bps=int(m.get("feeRate", 0) or 0),
                        metadata={
                            "volume24hr": m.get("volume24hr", 0),
                            "liquidity": m.get("liquidityNum", 0),
                            "competitive": m.get("competitive", 0),
                        },
                    )
                    db.add(market)
                    await db.flush()
                else:
                    # Update active status and metadata
                    market.is_active = not m.get("closed", False)
                    market.metadata = {
                        "volume24hr": m.get("volume24hr", 0),
                        "liquidity": m.get("liquidityNum", 0),
                        "competitive": m.get("competitive", 0),
                    }

                # Create market snapshot with current price
                best_bid = float(m.get("bestBid") or 0)
                best_ask = float(m.get("bestAsk") or 1)
                last_price = float(m.get("lastTradePrice") or 0)
                midpoint = (best_bid + best_ask) / 2 if best_bid and best_ask else last_price

                # Build book levels from outcomePrices (simplified - real book comes from CLOB)
                book_levels: dict = {"bids": [], "asks": []}
                if best_bid > 0:
                    book_levels["bids"] = [{"p": str(round(best_bid, 4)), "s": "100"}]
                if best_ask < 1:
                    book_levels["asks"] = [{"p": str(round(best_ask, 4)), "s": "100"}]

                snap = MarketSnapshot(
                    market_id=market.id,
                    best_bid=best_bid if best_bid > 0 else None,
                    best_ask=best_ask if best_ask < 1 else None,
                    midpoint=midpoint if midpoint > 0 else None,
                    spread=round(best_ask - best_bid, 4) if best_bid and best_ask else None,
                    last_trade_price=last_price if last_price > 0 else None,
                    volume_24h=float(m.get("volume24hr") or 0),
                    book_levels=book_levels,
                    source="polymarket_gamma",
                )
                db.add(snap)
                count += 1

            except Exception as e:
                logger.warning(f"Failed to process market {m.get('id')}: {e}")
                continue

        await db.commit()
        logger.info(f"Ingested {count} live markets")

    # Also refresh snapshots for all relationship markets (crypto short-term markets)
    await _refresh_relationship_market_snapshots()
    # Recalibrate spread baselines every ~10 market ingestion cycles
    import random as _rnd
    if _rnd.random() < 0.10:
        await _recalibrate_relationship_baselines()
    return count


async def _refresh_relationship_market_snapshots() -> None:
    """
    Pull fresh prices for markets in market_relationships.
    For real Polymarket markets: fetch from Gamma API.
    For demo crypto markets (polymarket_id starts with 'demo_'): simulate
    a realistic mean-reverting random walk so the actionability layer always
    has fresh data to work with.
    """
    from sqlalchemy import select
    from app.models.market import Market as MarketModel, MarketRelationship, MarketSnapshot
    import random

    async with async_session_factory() as db:
        rels = await db.execute(
            select(MarketRelationship).where(MarketRelationship.is_active.is_(True))
        )
        rel_market_ids: set = set()
        for r in rels.scalars().all():
            rel_market_ids.add(r.market_a_id)
            rel_market_ids.add(r.market_b_id)

        if not rel_market_ids:
            return

        mq = await db.execute(
            select(MarketModel).where(MarketModel.id.in_(list(rel_market_ids)))
        )
        rel_markets = mq.scalars().all()

        refreshed = 0
        now = datetime.now(timezone.utc)

        for market in rel_markets:
            try:
                is_demo = market.polymarket_id and market.polymarket_id.startswith("demo_")

                if is_demo:
                    # Get last snapshot for random walk continuity
                    last_snap = await db.execute(
                        select(MarketSnapshot)
                        .where(MarketSnapshot.market_id == market.id)
                        .order_by(MarketSnapshot.captured_at.desc())
                        .limit(1)
                    )
                    last = last_snap.scalar_one_or_none()
                    base = float(last.midpoint) if last and last.midpoint else 0.50

                    # Mean-reverting random walk around 0.50
                    # Window 2 markets update slightly slower → creates occasional dislocation
                    is_window_2 = "Window 2" in (market.question or "")
                    vol = 0.022 if "5m" in (market.question or "") else 0.015
                    drift = 0.08 * (0.50 - base)  # mean reversion
                    shock = random.gauss(0, vol) * (0.65 if is_window_2 else 1.0)
                    new_mid = max(0.03, min(0.97, base + drift + shock))

                    spread = round(random.uniform(0.022, 0.042), 4)
                    best_bid = max(0.01, round(new_mid - spread / 2, 4))
                    best_ask = min(0.99, round(new_mid + spread / 2, 4))
                    depth = random.uniform(220, 280)

                    snap = MarketSnapshot(
                        market_id=market.id,
                        captured_at=now,
                        best_bid=best_bid,
                        best_ask=best_ask,
                        midpoint=round(new_mid, 4),
                        spread=round(best_ask - best_bid, 4),
                        bid_depth=round(depth, 2),
                        ask_depth=round(depth * random.uniform(0.88, 1.12), 2),
                        last_trade_price=round(new_mid + random.gauss(0, 0.004), 4),
                        source="simulated_crypto",
                    )
                    db.add(snap)
                    refreshed += 1

                else:
                    # Real market — fetch from Gamma API
                    params = {}
                    if market.polymarket_id:
                        params = {"id": market.polymarket_id}
                    elif market.condition_id:
                        params = {"conditionId": market.condition_id}
                    else:
                        continue

                    async with httpx.AsyncClient(timeout=8.0) as client:
                        resp = await client.get(f"{GAMMA_API}/markets", params=params)
                        if resp.status_code != 200:
                            continue
                        data = resp.json()
                        items = data if isinstance(data, list) else [data]
                        if not items:
                            continue
                        m = items[0]
                        best_bid = float(m.get("bestBid") or 0)
                        best_ask = float(m.get("bestAsk") or 1)
                        last_price = float(m.get("lastTradePrice") or 0)
                        midpoint = (best_bid + best_ask) / 2 if best_bid and best_ask else last_price
                        if midpoint <= 0:
                            continue
                        snap = MarketSnapshot(
                            market_id=market.id,
                            captured_at=now,
                            best_bid=best_bid if best_bid > 0 else None,
                            best_ask=best_ask if best_ask > 0 else None,
                            midpoint=midpoint,
                            spread=round(best_ask - best_bid, 4) if best_bid and best_ask else None,
                            bid_depth=float(m.get("liquidityNum") or 100) / 2,
                            ask_depth=float(m.get("liquidityNum") or 100) / 2,
                            last_trade_price=last_price if last_price > 0 else None,
                            volume_24h=float(m.get("volume24hr") or 0) or None,
                            source="polymarket_gamma_relationship",
                        )
                        db.add(snap)
                        refreshed += 1
                        await asyncio.sleep(0.2)

            except Exception as e:
                logger.debug(f"Relationship market refresh failed for {market.question[:40]}: {e}")
                continue

        await db.commit()
        if refreshed > 0:
            logger.info(f"Refreshed snapshots for {refreshed} relationship markets")


async def _recalibrate_relationship_baselines() -> None:
    """
    Update normal_spread_mean and normal_spread_std for each relationship
    using the last 500 matched snapshot pairs. Runs occasionally to keep
    z-scores accurate as market behaviour evolves.
    """
    from sqlalchemy import select, text
    from app.models.market import MarketRelationship, MarketSnapshot

    async with async_session_factory() as db:
        rels_result = await db.execute(
            select(MarketRelationship).where(MarketRelationship.is_active.is_(True))
        )
        rels = rels_result.scalars().all()

        updated = 0
        for r in rels:
            try:
                rows = await db.execute(text("""
                    SELECT ABS(sa.midpoint - sb.midpoint) AS spread
                    FROM market_snapshots sa
                    JOIN market_snapshots sb
                      ON ABS(EXTRACT(EPOCH FROM (sa.captured_at - sb.captured_at))) < 60
                    WHERE sa.market_id = :mid_a AND sb.market_id = :mid_b
                    ORDER BY sa.captured_at DESC
                    LIMIT 500
                """), {"mid_a": str(r.market_a_id), "mid_b": str(r.market_b_id)})
                spreads = [float(row[0]) for row in rows if row[0] is not None]
                if len(spreads) < 20:
                    continue
                mean_s = sum(spreads) / len(spreads)
                var = sum((s - mean_s) ** 2 for s in spreads) / len(spreads)
                std_s = var ** 0.5
                if std_s < 0.001:
                    continue
                r.normal_spread_mean = round(mean_s, 4)
                r.normal_spread_std = round(std_s, 4)
                updated += 1
            except Exception:
                continue

        if updated:
            await db.commit()
            logger.info(f"Recalibrated baselines for {updated} market relationships")


async def _ensure_market_exists(db, condition_id: str, slug: str = "") -> "Market | None":
    """
    If a conditionId isn't in our markets table yet, fetch it from Gamma API
    and upsert it on-demand. This ensures wallet trades can always be linked.
    """
    from sqlalchemy import select
    if not condition_id:
        return None

    # Quick check first
    result = await db.execute(select(Market).where(Market.condition_id == condition_id))
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    # Fetch from Gamma API
    try:
        params: dict = {}
        if slug:
            params = {"slug": slug}
        else:
            params = {"conditionId": condition_id}

        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(f"{GAMMA_API}/markets", params=params)
            if resp.status_code != 200:
                return None
            data = resp.json()
            items = data if isinstance(data, list) else [data]
            if not items:
                return None
            m = items[0]

        import json
        token_ids = []
        try:
            token_ids = json.loads(m.get("clobTokenIds") or "[]")
        except Exception:
            pass

        market = Market(
            polymarket_id=str(m.get("id", "")),
            condition_id=m.get("conditionId", condition_id),
            question=m.get("question", ""),
            slug=m.get("slug", ""),
            outcomes=m.get("outcomes", '["Yes","No"]'),
            token_ids={"yes": token_ids[0] if len(token_ids) > 0 else "", "no": token_ids[1] if len(token_ids) > 1 else ""},
            category=m.get("category", "crypto"),
            is_active=not m.get("closed", False),
            end_date=datetime.fromisoformat(m["endDate"].replace("Z", "+00:00")) if m.get("endDate") else None,
            fees_enabled=bool(m.get("feesEnabled", False)),
            fee_rate_bps=int(m.get("feeRate", 0) or 0),
            metadata={"volume24hr": m.get("volume24hr", 0), "auto_discovered": True},
        )
        db.add(market)
        await db.flush()

        # Also add a snapshot
        best_bid = float(m.get("bestBid") or 0)
        best_ask = float(m.get("bestAsk") or 1)
        last_price = float(m.get("lastTradePrice") or 0)
        midpoint = (best_bid + best_ask) / 2 if best_bid and best_ask else last_price
        if midpoint > 0:
            snap = MarketSnapshot(
                market_id=market.id,
                best_bid=best_bid if best_bid > 0 else None,
                best_ask=best_ask if best_ask < 1 else None,
                midpoint=midpoint,
                spread=round(best_ask - best_bid, 4) if best_bid and best_ask else None,
                last_trade_price=last_price if last_price > 0 else None,
                volume_24h=float(m.get("volume24hr") or 0),
                source="polymarket_gamma_on_demand",
            )
            db.add(snap)

        logger.debug(f"Auto-discovered market: {m.get('question', '')[:60]}")
        return market

    except Exception as e:
        logger.debug(f"On-demand market fetch failed for {condition_id[:16]}: {e}")
        return None



async def ingest_wallet_activity(wallet_address: str) -> int:
    """
    Fetch and store recent trades for a specific wallet.
    Returns number of new trades ingested.
    """
    trades = await fetch_wallet_trades(wallet_address, limit=20)
    if not trades:
        return 0

    async with async_session_factory() as db:
        from sqlalchemy import select

        # Find or create wallet
        result = await db.execute(select(Wallet).where(Wallet.address == wallet_address))
        wallet = result.scalar_one_or_none()
        if not wallet:
            wallet = Wallet(address=wallet_address, label=f"{wallet_address[:10]}...", is_tracked=True)
            db.add(wallet)
            await db.flush()

        count = 0
        for trade in trades:
            try:
                # Deduplicate by tx hash
                tx_hash = trade.get("transactionHash") or trade.get("id") or ""
                if tx_hash:
                    dup = await db.execute(
                        select(WalletTransaction).where(WalletTransaction.source_sequence_id == tx_hash)
                    )
                    if dup.scalar_one_or_none():
                        continue

                # Store raw event
                await store_raw_event(
                    db=db,
                    source="polymarket_data_api",
                    event_type="wallet_trade",
                    payload={**trade, "_wallet": wallet_address},
                    source_timestamp=datetime.now(timezone.utc),
                )

                # Parse timestamps
                occurred_str = trade.get("timestamp") or trade.get("createdAt") or trade.get("time")
                occurred_at = None
                if occurred_str:
                    try:
                        if isinstance(occurred_str, (int, float)):
                            occurred_at = datetime.fromtimestamp(occurred_str, tz=timezone.utc)
                        else:
                            occurred_at = datetime.fromisoformat(str(occurred_str).replace("Z", "+00:00"))
                    except Exception:
                        occurred_at = datetime.now(timezone.utc)

                detected_at = datetime.now(timezone.utc)
                lag_ms = None
                if occurred_at:
                    raw_lag = int((detected_at - occurred_at).total_seconds() * 1000)
                    # Only store lag for recent trades (< 7 days); older ones get NULL
                    # Prevents integer overflow since detection_lag_ms is stored as INTEGER (int32 max ~24 days)
                    if 0 < raw_lag < 7 * 24 * 3600 * 1000:
                        lag_ms = raw_lag

                # Find market by condition id — try multiple field matches + auto-discover
                condition_id = trade.get("conditionId") or trade.get("market") or ""
                asset_id = trade.get("asset", "")
                slug = trade.get("slug", "")
                market_id = None
                if condition_id:
                    # Try 1: exact conditionId match
                    mkt_result = await db.execute(
                        select(Market).where(Market.condition_id == condition_id)
                    )
                    mkt = mkt_result.scalar_one_or_none()
                    # Try 2: polymarket_id (Gamma numeric id)
                    if not mkt:
                        mkt_result2 = await db.execute(
                            select(Market).where(Market.polymarket_id == condition_id)
                        )
                        mkt = mkt_result2.scalar_one_or_none()
                    # Try 3: token_ids JSONB search for asset token id
                    if not mkt and asset_id:
                        from sqlalchemy import text as sa_text
                        mkt_result3 = await db.execute(
                            select(Market).where(
                                sa_text("token_ids::text LIKE :pattern").bindparams(
                                    pattern=f"%{asset_id}%"
                                )
                            )
                        )
                        mkt = mkt_result3.scalar_one_or_none()
                    # Try 4: auto-discover from Gamma API (on-demand)
                    if not mkt:
                        mkt = await _ensure_market_exists(db, condition_id, slug=slug)
                    if mkt:
                        market_id = mkt.id

                side = "BUY" if str(trade.get("side", "buy")).upper() in ("BUY", "LONG") else "SELL"
                outcome = str(trade.get("outcome", trade.get("side", "Yes")))

                tx = WalletTransaction(
                    wallet_id=wallet.id,
                    market_id=market_id,
                    occurred_at=occurred_at,
                    detected_at=detected_at,
                    detection_lag_ms=lag_ms,
                    side=side,
                    outcome=outcome,
                    price=float(trade.get("price") or trade.get("avgPrice") or 0),
                    size=float(trade.get("size") or trade.get("amount") or 0),
                    notional=float(trade.get("usdcSize") or trade.get("value") or 0),
                    source="polymarket_data_api",
                    source_sequence_id=tx_hash or None,
                )
                db.add(tx)
                count += 1

            except Exception as e:
                logger.warning(f"Failed to process trade for {wallet_address}: {e}")
                continue

        await db.commit()
        logger.info(f"Ingested {count} new trades for {wallet_address[:10]}...")
        return count


async def run_live_ingestion_cycle() -> dict:
    """
    One full ingestion cycle:
    1. Fetch live markets
    2. Fetch wallet activity for tracked wallets  
    3. Normalize pending raw events
    """
    results: dict[str, Any] = {}

    # Step 1: Live markets
    try:
        results["markets_ingested"] = await ingest_live_markets()
    except Exception as e:
        logger.error(f"Market ingestion failed: {e}")
        results["markets_ingested"] = 0
        results["market_error"] = str(e)

    await asyncio.sleep(REQUEST_DELAY)

    # Step 2: Tracked wallet activity
    async with async_session_factory() as db:
        from sqlalchemy import select
        wallets_result = await db.execute(
            select(Wallet).where(Wallet.is_tracked == True)  # noqa: E712
        )
        tracked_wallets = wallets_result.scalars().all()

    wallet_trades = 0
    for wallet in tracked_wallets:
        # Skip demo wallets (addresses starting with 0xdemo)
        if wallet.address.startswith("0xdemo"):
            continue
        try:
            count = await ingest_wallet_activity(wallet.address)
            wallet_trades += count
            await asyncio.sleep(REQUEST_DELAY)
        except Exception as e:
            logger.warning(f"Wallet ingestion failed for {wallet.address}: {e}")

    results["wallet_trades_ingested"] = wallet_trades

    # Step 3: Normalize pending events
    try:
        async with async_session_factory() as db:
            normalized = await process_pending_events(db)
            await db.commit()
            results["events_normalized"] = normalized
    except Exception as e:
        logger.error(f"Event normalization failed: {e}")
        results["events_normalized"] = 0

    # Step 4: Run strategy engine — generate signals and execute paper trades
    try:
        from app.strategies.runner import StrategyRunner
        runner = StrategyRunner()
        async with async_session_factory() as db:
            strategy_stats = await runner.run_cycle(db)
            await db.commit()
            results["signals_generated"] = strategy_stats.get("signals_generated", 0)
            results["trades_executed"] = strategy_stats.get("trades_executed", 0)
            if strategy_stats.get("trades_executed", 0) > 0:
                logger.info(f"Strategy runner: {strategy_stats}")
    except Exception as e:
        logger.error(f"Strategy runner failed: {e}")
        results["signals_generated"] = 0
        results["trades_executed"] = 0

    # Step 5: Auto-discover new wallets from trade stream
    try:
        from app.intelligence.wallet_discovery import run_discovery_cycle
        discovery = await run_discovery_cycle()
        results["wallets_discovered"] = discovery.get("discovered", 0)
    except Exception as e:
        logger.error(f"Wallet discovery failed: {e}")
        results["wallets_discovered"] = 0

    # Step 6: Rescore wallets that have new trades
    if wallet_trades > 0:
        try:
            await rescore_active_wallets()
        except Exception as e:
            logger.warning(f"Wallet rescoring failed: {e}")

    # Step 7: Run exit engine — close positions that hit exit rules
    try:
        from app.execution.exit_engine import run_exit_cycle
        async with async_session_factory() as db:
            exit_result = await run_exit_cycle(db)
            results["positions_closed"] = exit_result.get("closed", 0)
            results["positions_checked"] = exit_result.get("checked", 0)
            if exit_result.get("closed", 0) > 0:
                logger.info(f"Exit engine closed {exit_result['closed']} positions")
    except Exception as e:
        logger.error(f"Exit engine failed: {e}")
        results["positions_closed"] = 0

    return results


async def rescore_active_wallets() -> None:
    """Rescore all real tracked wallets that have trade data."""
    from sqlalchemy import select
    from app.intelligence.wallet_scorer import score_and_persist

    async with async_session_factory() as db:
        result = await db.execute(
            select(Wallet).where(
                Wallet.is_tracked == True,  # noqa: E712
                ~Wallet.address.like("0xdemo%"),
            )
        )
        wallets = result.scalars().all()

        for wallet in wallets:
            try:
                await score_and_persist(db, wallet.id)
            except Exception as e:
                logger.debug(f"Rescore failed for {wallet.address[:10]}: {e}")

        try:
            await db.commit()
        except Exception:
            await db.rollback()


async def start_live_polling(
    market_interval: int = 30,
    wallet_interval: int = 60,
    discovery_interval: int = 120,
) -> None:
    """
    Start continuous live polling loop.
    market_interval: seconds between market refreshes
    wallet_interval: seconds between wallet activity checks
    discovery_interval: seconds between auto-discovery cycles
    """
    logger.info(
        f"Starting live Polymarket polling (markets/{market_interval}s, "
        f"wallets/{wallet_interval}s, discovery/{discovery_interval}s)"
    )

    last_wallet_poll = 0.0
    last_discovery = 0.0

    while True:
        try:
            now = asyncio.get_event_loop().time()
            do_wallets = (now - last_wallet_poll) >= wallet_interval
            do_discovery = (now - last_discovery) >= discovery_interval

            if do_wallets:
                result = await run_live_ingestion_cycle()
                last_wallet_poll = now
                if do_discovery:
                    last_discovery = now
            else:
                # Only markets this cycle
                try:
                    markets = await ingest_live_markets()
                    result = {"markets_ingested": markets}
                except Exception as e:
                    result = {"error": str(e)}

                # Run discovery on its own schedule even without wallet cycle
                if do_discovery:
                    try:
                        from app.intelligence.wallet_discovery import run_discovery_cycle
                        disc = await run_discovery_cycle()
                        result["wallets_discovered"] = disc.get("discovered", 0)
                        last_discovery = now
                    except Exception as e:
                        logger.warning(f"Discovery cycle error: {e}")

            logger.info(f"Live ingestion cycle: {result}")

        except Exception as e:
            logger.error(f"Live polling cycle error: {e}")

        await asyncio.sleep(market_interval)
