"""Normalize raw events from raw_events table into domain tables."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import RawEvent
from app.models.market import Market, MarketSnapshot
from app.models.trade import WalletTransaction
from app.models.wallet import Wallet


async def process_pending_events(db: AsyncSession, batch_size: int = 100) -> int:
    result = await db.execute(
        select(RawEvent)
        .where(RawEvent.processed.is_(False))
        .order_by(RawEvent.received_at.asc())
        .limit(batch_size)
    )
    events = result.scalars().all()
    processed = 0

    for event in events:
        try:
            await _normalize_event(db, event)
            event.processed = True
            processed += 1
        except Exception as e:
            event.process_error = str(e)[:500]
            event.processed = True

    await db.flush()
    return processed


async def _normalize_event(db: AsyncSession, event: RawEvent) -> None:
    if event.event_type == "trade":
        await _normalize_trade(db, event)
    elif event.event_type == "book":
        await _normalize_book(db, event)
    elif event.event_type == "price_change":
        await _normalize_price_change(db, event)
    elif event.event_type == "market_snapshot":
        await _normalize_snapshot(db, event)


async def _normalize_trade(db: AsyncSession, event: RawEvent) -> None:
    p = event.payload
    wallet_id = p.get("wallet_id")
    market_id = p.get("market_id")
    if not wallet_id or not market_id:
        return

    occurred_at = None
    if p.get("occurred_at"):
        occurred_at = datetime.fromisoformat(p["occurred_at"])

    detection_lag_ms = None
    if occurred_at and event.received_at:
        delta = event.received_at - occurred_at
        detection_lag_ms = int(delta.total_seconds() * 1000)

    tx = WalletTransaction(
        wallet_id=wallet_id,
        market_id=market_id,
        raw_event_id=event.id,
        occurred_at=occurred_at,
        detected_at=event.received_at,
        detection_lag_ms=detection_lag_ms,
        side=p.get("side"),
        outcome=p.get("outcome"),
        price=p.get("price"),
        size=p.get("size"),
        notional=p.get("notional"),
        tx_hash=p.get("tx_hash"),
        source=event.source,
        source_sequence_id=p.get("source_sequence_id"),
    )
    db.add(tx)


async def _normalize_book(db: AsyncSession, event: RawEvent) -> None:
    p = event.payload
    market_id = p.get("market_id")
    if not market_id:
        return

    bids = p.get("bids", [])
    asks = p.get("asks", [])
    best_bid = float(bids[0]["p"]) if bids else None
    best_ask = float(asks[0]["p"]) if asks else None
    midpoint = (best_bid + best_ask) / 2 if best_bid and best_ask else None
    spread = (best_ask - best_bid) if best_bid and best_ask else None
    bid_depth = sum(float(b.get("s", 0)) for b in bids)
    ask_depth = sum(float(a.get("s", 0)) for a in asks)

    snap = MarketSnapshot(
        market_id=market_id,
        raw_event_id=event.id,
        captured_at=event.source_timestamp or event.received_at,
        best_bid=best_bid,
        best_ask=best_ask,
        midpoint=midpoint,
        spread=spread,
        bid_depth=bid_depth,
        ask_depth=ask_depth,
        book_levels={"bids": bids, "asks": asks},
        source=event.source,
    )
    db.add(snap)


async def _normalize_price_change(db: AsyncSession, event: RawEvent) -> None:
    p = event.payload
    market_id = p.get("market_id")
    if not market_id:
        return

    snap = MarketSnapshot(
        market_id=market_id,
        raw_event_id=event.id,
        captured_at=event.source_timestamp or event.received_at,
        best_bid=p.get("best_bid"),
        best_ask=p.get("best_ask"),
        midpoint=p.get("midpoint"),
        spread=p.get("spread"),
        last_trade_price=p.get("last_trade_price"),
        source=event.source,
    )
    db.add(snap)


async def _normalize_snapshot(db: AsyncSession, event: RawEvent) -> None:
    p = event.payload
    market_id = p.get("market_id")
    if not market_id:
        return

    snap = MarketSnapshot(
        market_id=market_id,
        raw_event_id=event.id,
        captured_at=event.source_timestamp or event.received_at,
        best_bid=p.get("best_bid"),
        best_ask=p.get("best_ask"),
        midpoint=p.get("midpoint"),
        spread=p.get("spread"),
        bid_depth=p.get("bid_depth"),
        ask_depth=p.get("ask_depth"),
        book_levels=p.get("book_levels"),
        last_trade_price=p.get("last_trade_price"),
        volume_24h=p.get("volume_24h"),
        open_interest=p.get("open_interest"),
        source=event.source,
    )
    db.add(snap)
