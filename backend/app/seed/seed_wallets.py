"""Seed 10 demo wallets with different profiles."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.wallet import Wallet

DEMO_WALLETS = [
    {"address": "0xdemo_fast_whale_001", "label": "Fast Whale", "is_tracked": True},
    {"address": "0xdemo_steady_eddie_002", "label": "Steady Eddie", "is_tracked": True},
    {"address": "0xdemo_trap_wallet_003", "label": "Trap Wallet", "is_tracked": True},
    {"address": "0xdemo_market_maker_004", "label": "Likely Market Maker", "is_tracked": True},
    {"address": "0xdemo_arb_bot_005", "label": "Arb Bot Alpha", "is_tracked": True},
    {"address": "0xdemo_gambler_006", "label": "High Variance Gambler", "is_tracked": True},
    {"address": "0xdemo_slow_steady_007", "label": "Slow but Steady", "is_tracked": True},
    {"address": "0xdemo_whale_late_008", "label": "Late Whale", "is_tracked": False},
    {"address": "0xdemo_noise_009", "label": "Noise Trader", "is_tracked": False},
    {"address": "0xdemo_inactive_010", "label": "Inactive Wallet", "is_tracked": False},
]


async def seed_wallets(db: AsyncSession) -> list[Wallet]:
    wallets = []
    for data in DEMO_WALLETS:
        w = Wallet(**data)
        db.add(w)
        wallets.append(w)
    await db.flush()
    return wallets
