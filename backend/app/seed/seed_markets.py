"""Seed 20 demo markets for 5m/15m crypto prediction windows."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market import Market

now = datetime.now(timezone.utc)


def _make_markets() -> list[dict]:
    base_markets = []
    assets = ["BTC", "ETH", "SOL", "XRP"]
    windows = ["5m", "15m"]

    for asset in assets:
        for window in windows:
            for i in range(1, 3):
                slug = f"{asset.lower()}-up-{window}-window-{i}"
                base_markets.append({
                    "polymarket_id": f"demo_{slug}",
                    "condition_id": f"0xdemo_condition_{slug}",
                    "question": f"Will {asset} go up in the next {window}? (Window {i})",
                    "slug": slug,
                    "outcomes": ["Yes", "No"],
                    "token_ids": {
                        "yes": f"demo_token_{slug}_yes",
                        "no": f"demo_token_{slug}_no",
                    },
                    "category": "crypto",
                    "is_active": True,
                    "end_date": now + timedelta(hours=1),
                    "fees_enabled": True,
                    "fee_rate_bps": 150,
                })

    base_markets.extend([
        {
            "polymarket_id": "demo_btc_100k",
            "condition_id": "0xdemo_btc_100k",
            "question": "Will BTC reach $100k this month?",
            "slug": "btc-100k-this-month",
            "outcomes": ["Yes", "No"],
            "token_ids": {"yes": "demo_token_btc100k_yes", "no": "demo_token_btc100k_no"},
            "category": "crypto",
            "is_active": True,
            "end_date": now + timedelta(days=30),
            "fees_enabled": True,
            "fee_rate_bps": 250,
        },
        {
            "polymarket_id": "demo_eth_merge_impact",
            "condition_id": "0xdemo_eth_merge",
            "question": "Will ETH outperform BTC this week?",
            "slug": "eth-outperform-btc-week",
            "outcomes": ["Yes", "No"],
            "token_ids": {"yes": "demo_token_eth_out_yes", "no": "demo_token_eth_out_no"},
            "category": "crypto",
            "is_active": True,
            "end_date": now + timedelta(days=7),
            "fees_enabled": True,
            "fee_rate_bps": 250,
        },
        {
            "polymarket_id": "demo_sol_flip",
            "condition_id": "0xdemo_sol_flip",
            "question": "Will SOL flip XRP in market cap?",
            "slug": "sol-flip-xrp",
            "outcomes": ["Yes", "No"],
            "token_ids": {"yes": "demo_token_sol_flip_yes", "no": "demo_token_sol_flip_no"},
            "category": "crypto",
            "is_active": True,
            "end_date": now + timedelta(days=14),
            "fees_enabled": False,
            "fee_rate_bps": 0,
        },
        {
            "polymarket_id": "demo_fed_rate",
            "condition_id": "0xdemo_fed",
            "question": "Will the Fed cut rates in March?",
            "slug": "fed-rate-cut-march",
            "outcomes": ["Yes", "No"],
            "token_ids": {"yes": "demo_token_fed_yes", "no": "demo_token_fed_no"},
            "category": "politics",
            "is_active": True,
            "end_date": now + timedelta(days=21),
            "fees_enabled": False,
            "fee_rate_bps": 0,
        },
    ])

    return base_markets


async def seed_markets(db: AsyncSession) -> list[Market]:
    markets = []
    for data in _make_markets():
        m = Market(**data)
        db.add(m)
        markets.append(m)
    await db.flush()
    return markets
