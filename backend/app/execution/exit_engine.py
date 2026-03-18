"""
Exit engine: closes open paper positions based on smart, strategy-aware rules.

Exit priority by strategy type:

WALLET COPY (direct_copy, high_conviction, leader_copy):
  1. market_resolved     — market kapandı / resolved
  2. wallet_reversal     — kopyalanan wallet ters pozisyon aldı veya sattı
  3. ev_compression      — giriş edge'inin %70+ erimesi
  4. stop_loss           — % kayıp (STOP_LOSS_PCT, default 10%)
  5. target_hit          — %8 kâr
  6. stale_data          — snapshot yaşı (STALE_SNAPSHOT_HOURS, default 4h)
  7. max_hold_time       — son güvenlik ağı (24 saat)

DISLOCATION:
  1. market_resolved
  2. spread_normalized   — z-score normale döndü
  3. ev_compression
  4. stop_loss (STOP_LOSS_PCT)
  5. target_hit
  6. stale_data (STALE_SNAPSHOT_HOURS; STALE_SOFT_GUARD/STALE_EXIT_DISABLED)
  7. max_hold_time

DEMO_MODE_ONLY — never places real orders.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import assert_demo_mode, get_settings
from app.models.market import Market, MarketRelationship, MarketSnapshot
from app.models.paper import PaperPosition
from app.models.signal import TradeSignal
from app.models.trade import WalletTransaction

GAMMA_API = "https://gamma-api.polymarket.com"

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
MAX_HOLD_HOURS = 24          # prediction market edge can take hours to realize
TARGET_PCT = 0.08            # tighter: %8
# STOP_LOSS_PCT, STALE_SNAPSHOT_HOURS from config (env)

# EV Compression: close if current edge < entry_edge * this factor
EV_COMPRESSION_THRESHOLD = 0.30   # 70% eroded → exit

# Spread normalization: close dislocation if |z| drops below this
SPREAD_NORMALIZED_Z = 0.80

# Wallet reversal: look back this many minutes for wallet activity
WALLET_REVERSAL_LOOKBACK_MINUTES = 15
# Minimum hold time before wallet_reversal can trigger — avoids closing
# positions too quickly and guaranteeing a loss from fees + slippage.
WALLET_REVERSAL_MIN_HOLD_MINUTES = 30

# Strategy classification
COPY_STRATEGIES = {"direct_copy", "high_conviction", "leader_copy", "shadow"}
DISLOCATION_STRATEGIES = {"dislocation"}


async def _fetch_resolution_price(market: Market, side: str) -> float | None:
    """Fetch outcome price from Gamma API for a resolved/closed market.

    Returns the resolution-implied exit price for the given side:
    - BUY YES at 0.50 → market resolves YES → exit at 1.0
    - SELL YES at 0.50 → market resolves NO → exit at 0.0
    """
    import httpx
    pid = market.polymarket_id or market.condition_id
    if not pid:
        return None
    try:
        params = {"id": pid} if market.polymarket_id else {"conditionId": pid}
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{GAMMA_API}/markets", params=params)
            if resp.status_code != 200:
                return None
            data = resp.json()
            items = data if isinstance(data, list) else [data]
            if not items:
                return None
            m = items[0]
            prices = []
            try:
                prices = [float(p) for p in (m.get("outcomePrices") or "[]").strip("[]").split(",") if p.strip()]
            except Exception:
                pass
            if prices and len(prices) >= 1:
                yes_price = prices[0]
                if yes_price > 0.90 or yes_price < 0.10:
                    return yes_price
            best_bid = float(m.get("bestBid") or 0)
            best_ask = float(m.get("bestAsk") or 1)
            mid = (best_bid + best_ask) / 2 if best_bid and best_ask else float(m.get("lastTradePrice") or 0)
            if mid > 0.90 or mid < 0.10:
                return mid
    except Exception as e:
        logger.debug(f"Resolution price fetch failed: {e}")
    return None


async def get_latest_snapshot(
    db: AsyncSession, market_id: Any
) -> MarketSnapshot | None:
    result = await db.execute(
        select(MarketSnapshot)
        .where(MarketSnapshot.market_id == market_id)
        .order_by(MarketSnapshot.captured_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_latest_price(db: AsyncSession, market_id: Any) -> float | None:
    snap = await get_latest_snapshot(db, market_id)
    if snap and snap.midpoint:
        return float(snap.midpoint)
    if snap and snap.last_trade_price:
        return float(snap.last_trade_price)
    return None


async def close_position(
    db: AsyncSession,
    position: PaperPosition,
    exit_price: float,
    exit_reason: str,
) -> dict[str, Any]:
    """Mark a position as closed and compute realized PnL."""
    assert_demo_mode(get_settings())

    entry = float(position.avg_entry_price or 0)
    size = float(position.total_size or 0)
    fees = float(position.total_fees or 0)
    slippage = float(position.total_slippage or 0)

    if position.side == "BUY":
        gross_pnl = (exit_price - entry) * size
    else:
        gross_pnl = (entry - exit_price) * size

    realized_pnl = gross_pnl - fees - slippage

    position.avg_exit_price = exit_price
    position.realized_pnl = realized_pnl
    position.unrealized_pnl = 0.0
    position.exit_reason = exit_reason
    position.status = "closed"
    position.closed_at = datetime.now(timezone.utc)

    logger.info(
        f"Closed {position.strategy or 'unknown'} position {position.id} "
        f"via {exit_reason}: entry={entry:.4f} exit={exit_price:.4f} "
        f"pnl={realized_pnl:.4f}"
    )

    return {
        "position_id": str(position.id),
        "exit_reason": exit_reason,
        "exit_price": exit_price,
        "realized_pnl": realized_pnl,
        "strategy": position.strategy,
    }


async def _check_wallet_reversal(
    db: AsyncSession,
    position: PaperPosition,
    now: datetime,
) -> bool:
    """Return True if source wallet has reversed or exited this market.

    Reversal conditions:
    - wallet opened opposite side in same market after our entry
    - wallet sold/exited a significant position in same market after our entry
    """
    if position.source_wallet_id is None:
        return False

    cutoff = now - timedelta(minutes=WALLET_REVERSAL_LOOKBACK_MINUTES)
    entry_time = position.opened_at.replace(tzinfo=timezone.utc)

    result = await db.execute(
        select(WalletTransaction)
        .where(
            WalletTransaction.wallet_id == position.source_wallet_id,
            WalletTransaction.market_id == position.market_id,
            WalletTransaction.occurred_at > entry_time,
            WalletTransaction.occurred_at > cutoff,
        )
        .order_by(WalletTransaction.occurred_at.desc())
        .limit(5)
    )
    recent_txs = result.scalars().all()

    if not recent_txs:
        return False

    our_side = (position.side or "BUY").upper()
    opposite_side = "SELL" if our_side == "BUY" else "BUY"

    for tx in recent_txs:
        tx_side = (tx.side or "").upper()
        # Direct reversal: wallet opened the opposite side
        if tx_side == opposite_side:
            logger.info(
                f"Wallet reversal detected for position {position.id}: "
                f"wallet {str(position.source_wallet_id)[:8]} flipped to {tx_side}"
            )
            return True

    return False


async def _check_ev_compression(
    db: AsyncSession,
    position: PaperPosition,
    current_price: float,
    current_spread: float,
) -> bool:
    """Return True if edge has eroded to EV_COMPRESSION_THRESHOLD of entry edge.

    Fetches entry signal to get original net_edge, then recomputes current edge
    using live price and spread. If current_edge < entry_edge * threshold → exit.
    """
    # Try to find the original signal for this position via source_wallet_id + market
    # (PaperPosition doesn't store signal_id directly)
    if position.source_wallet_id is None:
        return False

    # Get the signal closest to position open time
    entry_time = position.opened_at.replace(tzinfo=timezone.utc)
    cutoff = entry_time - timedelta(seconds=10)

    result = await db.execute(
        select(TradeSignal)
        .where(
            TradeSignal.source_wallet_id == position.source_wallet_id,
            TradeSignal.market_id == position.market_id,
            TradeSignal.strategy == (position.strategy or "direct_copy"),
            TradeSignal.side == (position.side or "BUY"),
            TradeSignal.created_at >= cutoff,
            TradeSignal.created_at <= entry_time + timedelta(seconds=30),
        )
        .order_by(TradeSignal.created_at.desc())
        .limit(1)
    )
    signal = result.scalar_one_or_none()

    if signal is None or signal.net_edge is None:
        return False

    entry_edge = float(signal.net_edge)
    if entry_edge <= 0:
        # Entry edge was already non-positive — nothing to compress from
        return False

    # Recompute current edge: raw_edge minus current spread cost
    entry_price = float(position.avg_entry_price or 0)
    if entry_price <= 0:
        return False

    # Current edge depends on position direction:
    # BUY: we profit when price rises → edge = model_prob - current_price - spread/2
    # SELL: we profit when price falls → edge = current_price - model_prob - spread/2
    model_prob = float(signal.model_probability or entry_price)
    pos_side = (position.side or "BUY").upper()
    if pos_side == "SELL":
        current_edge = current_price - model_prob - (current_spread / 2)
    else:
        current_edge = model_prob - current_price - (current_spread / 2)

    if current_edge < entry_edge * EV_COMPRESSION_THRESHOLD:
        logger.info(
            f"EV compression for position {position.id}: "
            f"entry_edge={entry_edge:.4f} current_edge={current_edge:.4f} "
            f"({current_edge/entry_edge*100:.0f}% of entry)"
        )
        return True

    return False


async def _check_spread_normalized(
    db: AsyncSession,
    position: PaperPosition,
) -> bool:
    """Return True if the dislocation spread has normalized (z-score back to normal).

    Only applies to dislocation strategy positions.
    """
    if position.strategy not in DISLOCATION_STRATEGIES:
        return False

    # Find the market relationship for this position's market
    result = await db.execute(
        select(MarketRelationship)
        .where(
            MarketRelationship.is_active.is_(True),
            (MarketRelationship.market_a_id == position.market_id)
            | (MarketRelationship.market_b_id == position.market_id),
        )
        .limit(1)
    )
    rel = result.scalar_one_or_none()

    if rel is None or rel.normal_spread_mean is None or rel.normal_spread_std is None:
        return False

    # Get latest snapshots for both legs
    snap_a = await get_latest_snapshot(db, rel.market_a_id)
    snap_b = await get_latest_snapshot(db, rel.market_b_id)

    if snap_a is None or snap_b is None:
        return False

    price_a = float(snap_a.midpoint or 0.5)
    price_b = float(snap_b.midpoint or 0.5)
    current_spread = abs(price_a - price_b)

    mean = float(rel.normal_spread_mean)
    std = float(rel.normal_spread_std)

    if std <= 0:
        return False

    z_score = abs((current_spread - mean) / std)

    if z_score < SPREAD_NORMALIZED_Z:
        logger.info(
            f"Spread normalized for dislocation position {position.id}: "
            f"z={z_score:.2f} < threshold={SPREAD_NORMALIZED_Z}"
        )
        return True

    return False


async def run_exit_cycle(db: AsyncSession) -> dict[str, Any]:
    """
    Check all open positions and close those that hit an exit rule.

    Exit order by strategy:
    - Copy strategies: wallet_reversal → ev_compression → stop/target → stale → time
    - Dislocation: spread_normalized → ev_compression → stop/target → stale → time
    - Global: market_resolved always first, max_hold_time always last
    """
    assert_demo_mode(get_settings())

    now = datetime.now(timezone.utc)
    closed: list[dict] = []
    errors = 0

    result = await db.execute(
        select(PaperPosition).where(PaperPosition.status == "open")
    )
    positions = result.scalars().all()

    if not positions:
        return {"checked": 0, "closed": 0, "errors": 0}

    already_closed_this_cycle: set = set()

    market_ids = list({p.market_id for p in positions})
    mkt_result = await db.execute(
        select(Market).where(Market.id.in_(market_ids))
    )
    markets = {m.id: m for m in mkt_result.scalars().all()}

    for pos in positions:
        try:
            if pos.id in already_closed_this_cycle or pos.status == "closed":
                continue

            market = markets.get(pos.market_id)
            entry = float(pos.avg_entry_price or 0)
            size = float(pos.total_size or 0)

            if entry <= 0 or size <= 0:
                continue

            strategy = pos.strategy or "direct_copy"

            # ── Global Rule 1: Market resolved ────────────────────────────────
            # is_active=False means Polymarket has officially resolved the market.
            # Fetch actual resolution price from API (0 or 1) instead of stale
            # cached snapshot (which is likely the pre-resolution midpoint ~0.50).
            if market and not market.is_active:
                resolution_px = await _fetch_resolution_price(market, pos.side or "BUY")
                exit_price = resolution_px or await get_latest_price(db, pos.market_id) or entry
                info = await close_position(db, pos, exit_price, "market_resolved")
                closed.append(info)
                already_closed_this_cycle.add(pos.id)
                continue

            # ── Global Rule 1b: end_date grace period ────────────────────────
            # Prediction markets don't resolve instantly after end_date —
            # resolution can take minutes to hours. Closing at midpoint
            # guarantees a fee loss. Wait for actual resolution (is_active=False
            # or price near 0/1), or force-close after a grace period.
            if market and market.end_date:
                end_dt = market.end_date.replace(tzinfo=timezone.utc)
                if end_dt < now:
                    hours_past_end = (now - end_dt).total_seconds() / 3600
                    # Try API for resolution price first
                    resolution_px = await _fetch_resolution_price(market, pos.side or "BUY")
                    if resolution_px is not None:
                        info = await close_position(db, pos, resolution_px, "market_resolved")
                        closed.append(info)
                        already_closed_this_cycle.add(pos.id)
                        continue
                    # Check cached snapshot for extreme price
                    current_px = await get_latest_price(db, pos.market_id)
                    if current_px is not None and (current_px < 0.05 or current_px > 0.95):
                        info = await close_position(db, pos, current_px, "market_resolved")
                        closed.append(info)
                        already_closed_this_cycle.add(pos.id)
                        continue
                    # Grace period: force-close 3 hours after end_date
                    if hours_past_end > 3.0:
                        exit_price = current_px or entry
                        info = await close_position(db, pos, exit_price, "market_resolved")
                        closed.append(info)
                        already_closed_this_cycle.add(pos.id)
                        continue

            # ── Global Rule 2: Time limit (last resort) ───────────────────────
            age_hours = (now - pos.opened_at.replace(tzinfo=timezone.utc)).total_seconds() / 3600
            if age_hours > MAX_HOLD_HOURS:
                exit_price = await get_latest_price(db, pos.market_id) or entry
                info = await close_position(db, pos, exit_price, "max_hold_time")
                closed.append(info)
                already_closed_this_cycle.add(pos.id)
                continue

            # ── Get current price and spread ──────────────────────────────────
            snap = await get_latest_snapshot(db, pos.market_id)

            if snap is None:
                # No snapshot at all
                continue

            settings = get_settings()
            stale_threshold = float(settings.STALE_SNAPSHOT_HOURS)
            snap_age_h = (now - snap.captured_at.replace(tzinfo=timezone.utc)).total_seconds() / 3600
            is_stale = snap_age_h > stale_threshold

            if is_stale and not settings.STALE_EXIT_DISABLED and not settings.STALE_SOFT_GUARD:
                # Use last known price from stale snapshot, not entry — closing at entry
                # would lock in realized_pnl = -fees - slippage (guaranteed loss) while
                # the wallet may still hold and later close in profit.
                exit_price_stale = float(snap.midpoint or snap.last_trade_price or entry)
                logger.info(
                    f"stale_data exit: pos={pos.id} snap_age_h={snap_age_h:.2f} "
                    f"threshold={stale_threshold}h market={pos.market_id}"
                )
                info = await close_position(db, pos, exit_price_stale, "stale_data")
                closed.append(info)
                already_closed_this_cycle.add(pos.id)
                continue
            elif is_stale:
                logger.info(
                    f"stale_data skipped (soft_guard/disabled): pos={pos.id} snap_age_h={snap_age_h:.2f}h "
                    f"threshold={stale_threshold}h market={pos.market_id}"
                )
                # Soft guard: block stale EXIT but still check stop_loss/target/reversal below

            current_price = float(snap.midpoint or snap.last_trade_price or entry)
            current_spread = float(snap.spread or 0.04)

            # ────────────────────────────────────────────────────────────────
            # SMART EXIT v1 — Three layers only:
            #   Layer A: Signal invalidation (thesis no longer valid)
            #   Layer B: No-progress exit (trade isn't working)
            #   Layer C: Time-decay exit (tighten as resolution approaches)
            # ────────────────────────────────────────────────────────────────

            if pos.side == "BUY":
                unrealized = (current_price - entry) * size
            else:
                unrealized = (entry - current_price) * size
            pos.unrealized_pnl = unrealized

            notional = entry * size
            if notional <= 0:
                continue
            pnl_pct = unrealized / notional

            # Compute market lifetime progress (0.0 = just opened, 1.0 = at end_date)
            life_pct = 0.0
            if market and market.end_date:
                end_dt = market.end_date.replace(tzinfo=timezone.utc)
                total_life = (end_dt - pos.opened_at.replace(tzinfo=timezone.utc)).total_seconds()
                elapsed = (now - pos.opened_at.replace(tzinfo=timezone.utc)).total_seconds()
                if total_life > 0:
                    life_pct = min(elapsed / total_life, 2.0)

            # ── Layer A: Signal invalidation ──────────────────────────────
            # Entry thesis is no longer valid → exit regardless of PnL.

            # A1: Wallet reversal (copy strategies)
            if strategy in COPY_STRATEGIES:
                hold_minutes = (now - pos.opened_at.replace(tzinfo=timezone.utc)).total_seconds() / 60
                if hold_minutes >= WALLET_REVERSAL_MIN_HOLD_MINUTES:
                    if await _check_wallet_reversal(db, pos, now):
                        info = await close_position(db, pos, current_price, "wallet_reversal")
                        closed.append(info)
                        already_closed_this_cycle.add(pos.id)
                        continue

            # A2: Spread normalized (dislocation)
            if strategy in DISLOCATION_STRATEGIES:
                if await _check_spread_normalized(db, pos):
                    info = await close_position(db, pos, current_price, "spread_normalized")
                    closed.append(info)
                    already_closed_this_cycle.add(pos.id)
                    continue

            # A3: EV compression — entry edge eroded > 70%
            if await _check_ev_compression(db, pos, current_price, current_spread):
                info = await close_position(db, pos, current_price, "ev_compression")
                closed.append(info)
                already_closed_this_cycle.add(pos.id)
                continue

            # ── Layer B: No-progress exit ─────────────────────────────────
            # Trade has consumed significant lifetime without working.

            # B1: >35% lifetime used and pnl is still flat or negative
            if life_pct > 0.35 and pnl_pct < 0.01:
                info = await close_position(db, pos, current_price, "no_progress")
                closed.append(info)
                already_closed_this_cycle.add(pos.id)
                continue

            # ── Layer C: Time-decay exit ──────────────────────────────────
            # Tighten tolerance as market approaches resolution.

            # C1: Stop loss with time-decay tightening
            stop_pct = float(get_settings().STOP_LOSS_PCT)
            if life_pct > 0.85:
                stop_pct = min(stop_pct, 0.03)
            elif life_pct > 0.50:
                stop_pct = min(stop_pct, 0.06)

            if pnl_pct < -stop_pct:
                info = await close_position(db, pos, current_price, "stop_loss")
                closed.append(info)
                already_closed_this_cycle.add(pos.id)
                continue

            # C2: Target hit
            if pnl_pct > TARGET_PCT:
                info = await close_position(db, pos, current_price, "target_hit")
                closed.append(info)
                already_closed_this_cycle.add(pos.id)
                continue

        except Exception as e:
            logger.warning(f"Exit check failed for position {pos.id}: {e}")
            errors += 1

    if closed or errors == 0:
        try:
            await db.commit()
        except Exception as e:
            logger.error(f"Exit cycle commit failed: {e}")
            await db.rollback()

    return {
        "checked": len(positions),
        "closed": len(closed),
        "errors": errors,
        "details": closed,
        "exit_breakdown": _summarize_exits(closed),
    }


def _summarize_exits(closed: list[dict]) -> dict[str, int]:
    """Count exits by reason for analytics."""
    summary: dict[str, int] = {}
    for c in closed:
        reason = c.get("exit_reason", "unknown")
        summary[reason] = summary.get(reason, 0) + 1
    return summary
