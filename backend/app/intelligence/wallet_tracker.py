"""Track selected wallets, detect new trades, compute detection lag."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

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
    """Find wallet trades we detected after `since` (using detected_at = when we ingested).
    Polymarket API returns trades with occurred_at from hours ago; detected_at reflects
    when we first saw them, so we process newly discovered trades regardless of on-chain time."""
    q = select(WalletTransaction).where(WalletTransaction.wallet_id == wallet_id)
    if since:
        q = q.where(WalletTransaction.detected_at >= since)
    q = q.order_by(WalletTransaction.detected_at.desc()).limit(50)
    result = await db.execute(q)
    return list(result.scalars().all())


async def detect_new_trades_batch(
    db: AsyncSession,
    wallet_ids: list[UUID],
    since: datetime,
    limit_per_wallet: int = 3,
) -> dict[UUID, list[WalletTransaction]]:
    """Fetch new trades for ALL wallets in a single query.

    Returns a dict mapping wallet_id → list of WalletTransaction.
    Much faster than calling detect_new_trades() per wallet.
    """
    if not wallet_ids:
        return {}

    # Single query: all wallets, filtered by since, market_id not null
    from sqlalchemy import and_, text
    from sqlalchemy.dialects.postgresql import array

    q = (
        select(WalletTransaction)
        .where(
            WalletTransaction.wallet_id.in_(wallet_ids),
            WalletTransaction.detected_at >= since,
            WalletTransaction.market_id.isnot(None),
        )
        .order_by(WalletTransaction.wallet_id, WalletTransaction.detected_at.desc())
    )
    result = await db.execute(q)
    all_txs = list(result.scalars().all())

    # Group by wallet_id, keep only limit_per_wallet per wallet
    grouped: dict[UUID, list[WalletTransaction]] = {}
    for tx in all_txs:
        wid = tx.wallet_id
        if wid not in grouped:
            grouped[wid] = []
        if len(grouped[wid]) < limit_per_wallet:
            grouped[wid].append(tx)

    return grouped


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
