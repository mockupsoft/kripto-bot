"""Track selected wallets, detect new trades, compute detection lag."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trade import WalletTransaction
from app.models.wallet import Wallet


async def get_tracked_wallets(db: AsyncSession) -> list[Wallet]:
    result = await db.execute(select(Wallet).where(Wallet.is_tracked.is_(True)))
    return list(result.scalars().all())


async def detect_new_trades(
    db: AsyncSession,
    wallet_id,
    since: datetime | None = None,
) -> list[WalletTransaction]:
    """Find wallet trades that occurred after `since` (using occurred_at for recency)."""
    q = select(WalletTransaction).where(WalletTransaction.wallet_id == wallet_id)
    if since:
        q = q.where(WalletTransaction.occurred_at > since)
    q = q.order_by(WalletTransaction.occurred_at.desc()).limit(50)
    result = await db.execute(q)
    return list(result.scalars().all())


async def get_wallet_trade_summary(db: AsyncSession, wallet_id) -> dict:
    """Summary stats for a tracked wallet's recent activity."""
    count_q = select(func.count()).where(WalletTransaction.wallet_id == wallet_id)
    total = (await db.execute(count_q)).scalar() or 0

    avg_lag_q = select(func.avg(WalletTransaction.detection_lag_ms)).where(
        WalletTransaction.wallet_id == wallet_id,
        WalletTransaction.detection_lag_ms.isnot(None),
    )
    avg_lag = (await db.execute(avg_lag_q)).scalar()

    latest_q = (
        select(WalletTransaction)
        .where(WalletTransaction.wallet_id == wallet_id)
        .order_by(WalletTransaction.detected_at.desc())
        .limit(1)
    )
    latest = (await db.execute(latest_q)).scalar_one_or_none()

    return {
        "total_trades": total,
        "avg_detection_lag_ms": int(avg_lag) if avg_lag else None,
        "latest_trade_at": latest.detected_at.isoformat() if latest else None,
    }
