"""Generate 2000+ realistic demo events over a 48-hour window for all 5 scenarios."""

from __future__ import annotations

import random
from datetime import datetime, timezone, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import RawEvent
from app.models.market import Market
from app.models.wallet import Wallet


async def seed_events(
    db: AsyncSession,
    wallets: list[Wallet],
    markets: list[Market],
    rng_seed: int = 42,
) -> int:
    rng = random.Random(rng_seed)
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=48)
    events = []

    market_by_slug = {m.slug: m for m in markets if m.slug}
    wallet_by_label = {w.label: w for w in wallets if w.label}

    crypto_markets = [m for m in markets if m.category == "crypto" and "5m" in (m.slug or "")]
    all_crypto = [m for m in markets if m.category == "crypto"]

    def _ts(offset_minutes: float) -> datetime:
        return start + timedelta(minutes=offset_minutes)

    def _book(mid: float, spread: float, depth_per_level: float = 50) -> dict:
        half = spread / 2
        bids = [{"p": f"{mid - half - i * 0.01:.4f}", "s": f"{depth_per_level + rng.uniform(-10, 10):.1f}"} for i in range(5)]
        asks = [{"p": f"{mid + half + i * 0.01:.4f}", "s": f"{depth_per_level + rng.uniform(-10, 10):.1f}"} for i in range(5)]
        return {"bids": bids, "asks": asks}

    # --- Generate market snapshot events every ~15 minutes for all crypto markets ---
    t_offset = 0.0
    while t_offset < 48 * 60:
        for m in all_crypto:
            mid = 0.50 + rng.gauss(0, 0.08)
            mid = max(0.05, min(0.95, mid))
            spr = rng.uniform(0.02, 0.06)
            book = _book(mid, spr)
            events.append(RawEvent(
                received_at=_ts(t_offset + rng.uniform(0, 1)),
                source="mock",
                event_type="book",
                source_timestamp=_ts(t_offset),
                payload={
                    "market_id": str(m.id),
                    "bids": book["bids"],
                    "asks": book["asks"],
                    "timestamp": str(int(_ts(t_offset).timestamp() * 1000)),
                },
            ))
        t_offset += 15

    # --- Scenario A: Fast Whale trades (edge disappears in <200ms) ---
    fw = wallet_by_label.get("Fast Whale")
    if fw and crypto_markets:
        for i in range(40):
            t = rng.uniform(60, 48 * 60 - 60)
            m = rng.choice(crypto_markets)
            price = rng.uniform(0.40, 0.60)
            size = rng.uniform(200, 800)
            events.append(RawEvent(
                received_at=_ts(t + rng.uniform(0.003, 0.01)),  # very fast detection
                source="mock",
                event_type="trade",
                source_timestamp=_ts(t),
                payload={
                    "wallet_id": str(fw.id),
                    "market_id": str(m.id),
                    "side": rng.choice(["BUY", "SELL"]),
                    "outcome": "Yes",
                    "price": round(price, 4),
                    "size": round(size, 2),
                    "notional": round(price * size, 2),
                    "occurred_at": _ts(t).isoformat(),
                    "source_sequence_id": f"fw_{i}",
                },
            ))

    # --- Scenario B: Steady Eddie trades (consistent, edge persists) ---
    se = wallet_by_label.get("Steady Eddie")
    if se and crypto_markets:
        for i in range(80):
            t = rng.uniform(30, 48 * 60 - 30)
            m = rng.choice(crypto_markets)
            price = rng.uniform(0.42, 0.58)
            size = rng.uniform(30, 90)
            events.append(RawEvent(
                received_at=_ts(t + rng.uniform(2, 8)),  # slower detection
                source="mock",
                event_type="trade",
                source_timestamp=_ts(t),
                payload={
                    "wallet_id": str(se.id),
                    "market_id": str(m.id),
                    "side": rng.choice(["BUY", "BUY", "BUY", "SELL"]),  # biased toward BUY
                    "outcome": "Yes",
                    "price": round(price, 4),
                    "size": round(size, 2),
                    "notional": round(price * size, 2),
                    "occurred_at": _ts(t).isoformat(),
                    "source_sequence_id": f"se_{i}",
                },
            ))

    # --- Scenario C: Dislocation window (BTC 5m/15m spread divergence) ---
    m5 = market_by_slug.get("btc-up-5m-window-1")
    m15 = market_by_slug.get("btc-up-15m-window-1")
    if m5 and m15:
        dislocation_start = rng.uniform(600, 1200)
        for i in range(60):
            t = dislocation_start + i * 0.5
            mid_5m = 0.55 + 0.08 * (i / 60)
            mid_15m = 0.53 + 0.02 * (i / 60)
            for m_id, mid in [(str(m5.id), mid_5m), (str(m15.id), mid_15m)]:
                spr = rng.uniform(0.02, 0.04)
                book = _book(mid, spr, depth_per_level=rng.uniform(20, 60))
                events.append(RawEvent(
                    received_at=_ts(t + rng.uniform(0, 0.2)),
                    source="mock",
                    event_type="book",
                    source_timestamp=_ts(t),
                    payload={
                        "market_id": m_id,
                        "bids": book["bids"],
                        "asks": book["asks"],
                        "timestamp": str(int(_ts(t).timestamp() * 1000)),
                    },
                ))

    # --- Scenario D: Trap Wallet (profitable then crashes) ---
    tw = wallet_by_label.get("Trap Wallet")
    if tw and crypto_markets:
        for i in range(20):
            t = rng.uniform(60, 1200)
            m = rng.choice(crypto_markets)
            events.append(RawEvent(
                received_at=_ts(t + rng.uniform(3, 10)),
                source="mock",
                event_type="trade",
                source_timestamp=_ts(t),
                payload={
                    "wallet_id": str(tw.id),
                    "market_id": str(m.id),
                    "side": "BUY",
                    "outcome": "Yes",
                    "price": round(rng.uniform(0.40, 0.50), 4),
                    "size": round(rng.uniform(40, 100), 2),
                    "notional": 50.0,
                    "occurred_at": _ts(t).isoformat(),
                    "source_sequence_id": f"tw_win_{i}",
                },
            ))
        for i in range(3):
            t = 1300 + i * 30
            m = rng.choice(crypto_markets)
            events.append(RawEvent(
                received_at=_ts(t + rng.uniform(3, 10)),
                source="mock",
                event_type="trade",
                source_timestamp=_ts(t),
                payload={
                    "wallet_id": str(tw.id),
                    "market_id": str(m.id),
                    "side": "BUY",
                    "outcome": "Yes",
                    "price": round(rng.uniform(0.70, 0.85), 4),
                    "size": round(rng.uniform(300, 600), 2),
                    "notional": 400.0,
                    "occurred_at": _ts(t).isoformat(),
                    "source_sequence_id": f"tw_loss_{i}",
                },
            ))

    # --- Scenario E: Ghost Liquidity (thin book on second leg) ---
    if m5 and m15:
        ghost_t = rng.uniform(1800, 2200)
        events.append(RawEvent(
            received_at=_ts(ghost_t),
            source="mock",
            event_type="book",
            source_timestamp=_ts(ghost_t),
            payload={
                "market_id": str(m15.id),
                "bids": [{"p": "0.4800", "s": "8.0"}, {"p": "0.4700", "s": "7.0"}],
                "asks": [{"p": "0.5200", "s": "8.0"}, {"p": "0.5300", "s": "7.0"}],
                "timestamp": str(int(_ts(ghost_t).timestamp() * 1000)),
            },
        ))

    # --- General noise trades from other wallets ---
    noise_wallets = [w for w in wallets if w.label and "Noise" in w.label or w.label and "Gambler" in w.label]
    for nw in noise_wallets:
        for i in range(rng.randint(20, 50)):
            t = rng.uniform(0, 48 * 60)
            m = rng.choice(all_crypto)
            events.append(RawEvent(
                received_at=_ts(t + rng.uniform(2, 15)),
                source="mock",
                event_type="trade",
                source_timestamp=_ts(t),
                payload={
                    "wallet_id": str(nw.id),
                    "market_id": str(m.id),
                    "side": rng.choice(["BUY", "SELL"]),
                    "outcome": rng.choice(["Yes", "No"]),
                    "price": round(rng.uniform(0.10, 0.90), 4),
                    "size": round(rng.uniform(10, 200), 2),
                    "notional": 50.0,
                    "occurred_at": _ts(t).isoformat(),
                    "source_sequence_id": f"noise_{nw.address[-3:]}_{i}",
                },
            ))

    db.add_all(events)
    await db.flush()
    return len(events)
