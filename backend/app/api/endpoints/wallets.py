from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, case, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models.market import Market
from app.models.paper import PaperPosition
from app.models.wallet import Wallet, WalletScore
from app.models.trade import WalletTransaction

router = APIRouter()


@router.get("")
async def list_wallets(
    limit: int = Query(50, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict:
    # Use DISTINCT ON via raw subquery for efficiency — avoids full group-by scan
    from sqlalchemy import text

    subq = (
        select(
            WalletScore.wallet_id,
            func.max(WalletScore.scored_at).label("latest"),
        )
        .group_by(WalletScore.wallet_id)
        .subquery()
    )

    query = (
        select(Wallet, WalletScore)
        .outerjoin(subq, Wallet.id == subq.c.wallet_id)
        .outerjoin(
            WalletScore,
            (WalletScore.wallet_id == subq.c.wallet_id) & (WalletScore.scored_at == subq.c.latest),
        )
        .where(Wallet.is_tracked.is_(True))
        .order_by(WalletScore.composite_score.desc().nullslast())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(query)
    rows = result.all()

    wallet_ids = [w.id for w, _ in rows]

    # Batch-fetch last trade time per wallet using detected_at (indexed) instead of occurred_at
    last_trade_q = await db.execute(
        select(WalletTransaction.wallet_id, func.max(WalletTransaction.detected_at).label("last_trade"))
        .where(WalletTransaction.wallet_id.in_(wallet_ids))
        .group_by(WalletTransaction.wallet_id)
    )
    last_trade_map = {str(r.wallet_id): r.last_trade for r in last_trade_q}

    # Batch-fetch copy PnL summary per wallet
    copy_q = await db.execute(
        select(
            PaperPosition.source_wallet_id,
            func.count().label("trade_count"),
            func.sum(PaperPosition.realized_pnl).label("total_pnl"),
            func.sum(case((PaperPosition.realized_pnl > 0, 1), else_=0)).label("wins"),
            func.max(PaperPosition.opened_at).label("last_copied_at"),
        )
        .where(
            PaperPosition.source_wallet_id.in_(wallet_ids),
            PaperPosition.status == "closed",
        )
        .group_by(PaperPosition.source_wallet_id)
    )
    copy_map = {str(r.source_wallet_id): r for r in copy_q}

    now = datetime.now(timezone.utc)

    def _activity_label(last_at: datetime | None) -> str:
        if not last_at:
            return "UNKNOWN"
        last_at = last_at.replace(tzinfo=timezone.utc) if last_at.tzinfo is None else last_at
        delta = now - last_at
        if delta < timedelta(hours=24):
            return "ACTIVE"
        if delta < timedelta(days=7):
            return "WARM"
        return "DORMANT"

    wallets_out = []
    for w, s in rows:
        wid = str(w.id)
        last_trade = last_trade_map.get(wid)
        cp = copy_map.get(wid)
        trade_count = int(cp.trade_count) if cp else 0
        total_pnl = float(cp.total_pnl or 0) if cp else 0.0
        wins = int(cp.wins or 0) if cp else 0
        copy_win_rate = (wins / trade_count) if trade_count > 0 else None
        avg_pnl = (total_pnl / trade_count) if trade_count > 0 else None
        last_copied_at = cp.last_copied_at.isoformat() if cp and cp.last_copied_at else None

        wallets_out.append({
            "id": wid,
            "address": w.address,
            "label": w.label,
            "is_tracked": w.is_tracked,
            "score": {
                "composite": float(s.composite_score) if s and s.composite_score is not None else None,
                "copyability": float(s.copyability_score) if s and s.copyability_score is not None else None,
                "roi": float(s.total_roi) if s and s.total_roi is not None else None,
                "hit_rate": float(s.hit_rate) if s and s.hit_rate is not None else None,
                "max_drawdown": float(s.max_drawdown) if s and s.max_drawdown is not None else None,
                "classification": s.classification if s else None,
                "copy_decay_curve": s.copy_decay_curve if s else {},
                "scored_at": s.scored_at.isoformat() if s and s.scored_at else None,
            } if s else None,
            "live_status": {
                "last_trade_at": last_trade.isoformat() if last_trade else None,
                "activity_label": _activity_label(last_trade),
            },
            "copy_performance": {
                "copied_trade_count": trade_count,
                "copied_realized_pnl": round(total_pnl, 4),
                "copied_win_rate": round(copy_win_rate, 4) if copy_win_rate is not None else None,
                "copied_avg_pnl": round(avg_pnl, 4) if avg_pnl is not None else None,
                "last_copied_at": last_copied_at,
            },
        })

    return {"wallets": wallets_out}


@router.get("/alpha-leaderboard")
async def get_alpha_leaderboard(
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Copyable Alpha leaderboard.
    Shows wallets ranked by copyability × ROI, with full latency decay breakdown.
    This is the killer feature: not just who made money, but who is actually copyable.
    """
    from sqlalchemy import desc, and_

    subq = (
        select(
            WalletScore.wallet_id,
            func.max(WalletScore.scored_at).label("latest"),
        )
        .group_by(WalletScore.wallet_id)
        .subquery()
    )

    query = (
        select(Wallet, WalletScore)
        .join(subq, Wallet.id == subq.c.wallet_id)
        .join(
            WalletScore,
            and_(
                WalletScore.wallet_id == subq.c.wallet_id,
                WalletScore.scored_at == subq.c.latest,
            ),
        )
        .where(Wallet.is_tracked == True)  # noqa: E712
        .order_by(
            (WalletScore.copyability_score * WalletScore.composite_score).desc()
        )
        .limit(limit)
    )
    result = await db.execute(query)
    rows = result.all()

    leaderboard = []
    for i, (w, s) in enumerate(rows):
        decay = s.copy_decay_curve or {}
        copyable_alpha = round(
            float(s.copyability_score or 0) * float(s.total_roi or 0), 4
        )
        leaderboard.append({
            "rank": i + 1,
            "wallet_id": str(w.id),
            "address": w.address,
            "label": w.label,
            "is_real": not w.address.startswith("0xdemo"),
            "roi": float(s.total_roi or 0),
            "hit_rate": float(s.hit_rate or 0),
            "copyability_score": float(s.copyability_score or 0),
            "composite_score": float(s.composite_score or 0),
            "copyable_alpha": copyable_alpha,
            "classification": s.classification,
            "max_drawdown": float(s.max_drawdown or 0),
            "consistency": float(s.consistency_score or 0),
            "suspiciousness": float(s.suspiciousness_score or 0),
            "latency_decay": {
                "200ms": float(decay.get("200ms", 0)),
                "500ms": float(decay.get("500ms", 0)),
                "1000ms": float(decay.get("1000ms", 0)),
                "1500ms": float(decay.get("1500ms", 0)),
            },
            "verdict": _copyability_verdict(
                float(s.copyability_score or 0),
                float(s.total_roi or 0),
                decay,
            ),
        })

    return {
        "leaderboard": leaderboard,
        "total": len(leaderboard),
        "explanation": "Ranked by copyability × composite_score. High ROI + low copyability = not actionable.",
    }


@router.get("/worst-by-copy-pnl")
async def get_worst_by_copy_pnl(
    limit: int = Query(20, le=100),
    lookback_hours: int = Query(168, ge=24, le=720),
    min_trades: int = Query(10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Wallets ranked by worst copy PnL (for manual blacklist decisions).
    Policy: trade_count >= min_trades olmayan wallet asla suggested_blacklist'e girmez.
    """
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=lookback_hours)

    agg = (
        select(
            PaperPosition.source_wallet_id,
            func.count().label("trade_count"),
            func.sum(PaperPosition.realized_pnl).label("total_pnl"),
            func.sum(case((PaperPosition.realized_pnl > 0, 1), else_=0)).label("wins"),
            func.sum(case((PaperPosition.realized_pnl > 0, PaperPosition.realized_pnl), else_=0)).label("gross_profit"),
            func.sum(case((PaperPosition.realized_pnl < 0, PaperPosition.realized_pnl), else_=0)).label("gross_loss_raw"),
        )
        .where(
            PaperPosition.source_wallet_id.isnot(None),
            PaperPosition.status == "closed",
            PaperPosition.closed_at >= since,
        )
        .group_by(PaperPosition.source_wallet_id)
    )
    agg_result = await db.execute(agg)
    rows = agg_result.all()

    # Filter: trade_count >= min_trades
    eligible = [
        r for r in rows
        if int(r.trade_count or 0) >= min_trades
    ]
    wins_map = {str(r.source_wallet_id): int(r.wins or 0) for r in eligible}
    total_pnl_map = {str(r.source_wallet_id): float(r.total_pnl or 0) for r in eligible}
    trade_count_map = {str(r.source_wallet_id): int(r.trade_count or 0) for r in eligible}
    gross_profit_map = {str(r.source_wallet_id): float(r.gross_profit or 0) for r in eligible}
    gross_loss_map = {str(r.source_wallet_id): abs(float(r.gross_loss_raw or 0)) for r in eligible}

    # Sort by total_pnl ASC (worst first), then avg_pnl for tie-break
    def _sort_key(wid: str) -> tuple[float, float]:
        pnl = total_pnl_map[wid]
        cnt = trade_count_map[wid]
        avg = pnl / cnt if cnt else 0.0
        return (pnl, avg)

    sorted_ids = sorted(eligible, key=lambda r: _sort_key(str(r.source_wallet_id)))
    top_wallet_ids = [str(r.source_wallet_id) for r in sorted_ids[:limit]]

    if not top_wallet_ids:
        return {
            "worst_wallets": [],
            "suggested_blacklist": [],
            "min_trades": min_trades,
            "lookback_hours": lookback_hours,
            "note": f"No wallets with trade_count >= {min_trades} in lookback window.",
        }

    # Fetch Wallet + latest WalletScore for top wallets
    wallet_result = await db.execute(select(Wallet).where(Wallet.id.in_([UUID(w) for w in top_wallet_ids])))
    wallets_by_id = {str(w.id): w for w in wallet_result.scalars().all()}

    score_subq = (
        select(WalletScore.wallet_id, func.max(WalletScore.scored_at).label("latest"))
        .where(WalletScore.wallet_id.in_([UUID(w) for w in top_wallet_ids]))
        .group_by(WalletScore.wallet_id)
        .subquery()
    )
    score_result = await db.execute(
        select(WalletScore)
        .join(score_subq, (WalletScore.wallet_id == score_subq.c.wallet_id) & (WalletScore.scored_at == score_subq.c.latest))
        .where(WalletScore.wallet_id.in_([UUID(w) for w in top_wallet_ids]))
    )
    scores_by_id = {str(s.wallet_id): s for s in score_result.scalars().all()}

    worst_wallets = []
    for wid in top_wallet_ids:
        w = wallets_by_id.get(wid)
        s = scores_by_id.get(wid)
        cnt = trade_count_map.get(wid, 0)
        pnl = total_pnl_map.get(wid, 0.0)
        wins = wins_map.get(wid, 0)
        wr = wins / cnt if cnt else 0.0
        avg_pnl = pnl / cnt if cnt else 0.0
        gross_profit = gross_profit_map.get(wid, 0.0)
        gross_loss = gross_loss_map.get(wid, 0.0)
        pf = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else None)
        loss_rate = 1 - wr
        avg_win = gross_profit / wins if wins else 0.0
        avg_loss = gross_loss / (cnt - wins) if (cnt - wins) else 0.0
        expectancy = (wr * avg_win) + (loss_rate * avg_loss) if cnt else 0.0

        worst_wallets.append({
            "wallet_id": wid,
            "address": w.address if w else None,
            "label": w.label if w else None,
            "total_pnl": round(pnl, 4),
            "trade_count": cnt,
            "win_rate": round(wr, 4),
            "avg_pnl_per_trade": round(avg_pnl, 4),
            "profit_factor": round(pf, 3) if pf is not None else None,
            "expectancy": round(expectancy, 4),
            "composite_score": float(s.composite_score) if s and s.composite_score is not None else None,
            "copyability_score": float(s.copyability_score) if s and s.copyability_score is not None else None,
        })

    # suggested_blacklist: only trade_count >= min_trades (already filtered); top 5-10
    suggested_blacklist = top_wallet_ids[:10]

    return {
        "worst_wallets": worst_wallets,
        "suggested_blacklist": suggested_blacklist,
        "min_trades": min_trades,
        "lookback_hours": lookback_hours,
    }


def _copyability_verdict(copyability: float, roi: float, decay: dict) -> str:
    """Plain-language verdict on whether this wallet is worth copying."""
    decay_500 = float(decay.get("500ms", 0))
    if copyability < 0.2:
        return "NOT_COPYABLE: edge evaporates before you can act"
    if roi < 0 and copyability > 0.5:
        return "AVOID: copyable but unprofitable"
    if decay_500 < 0.3:
        return "TOO_FAST: edge dies at 500ms, needs sub-200ms execution"
    if copyability >= 0.6 and roi > 0.05:
        return "STRONG_CANDIDATE: copyable and profitable"
    if copyability >= 0.4 and roi > 0:
        return "MODERATE: worth watching, borderline copyable"
    return "WEAK: low confidence"


@router.get("/{wallet_id}")
async def get_wallet(wallet_id: UUID, db: AsyncSession = Depends(get_db)) -> dict:
    wallet = await db.get(Wallet, wallet_id)
    if not wallet:
        return {"error": "not found"}

    score_result = await db.execute(
        select(WalletScore)
        .where(WalletScore.wallet_id == wallet_id)
        .order_by(WalletScore.scored_at.desc())
        .limit(1)
    )
    score = score_result.scalar_one_or_none()

    return {
        "id": str(wallet.id),
        "address": wallet.address,
        "proxy_address": wallet.proxy_address,
        "label": wallet.label,
        "is_tracked": wallet.is_tracked,
        "score": {
            "composite": float(score.composite_score) if score and score.composite_score is not None else None,
            "copyability": float(score.copyability_score) if score and score.copyability_score is not None else None,
            "total_roi": float(score.total_roi) if score and score.total_roi is not None else None,
            "hit_rate": float(score.hit_rate) if score and score.hit_rate is not None else None,
            "max_drawdown": float(score.max_drawdown) if score and score.max_drawdown is not None else None,
            "classification": score.classification if score else None,
            "consistency": float(score.consistency_score) if score and score.consistency_score is not None else None,
            "suspiciousness": float(score.suspiciousness_score) if score and score.suspiciousness_score is not None else None,
            "copy_decay_curve": score.copy_decay_curve if score else {},
            "explanation": score.explanation if score else {},
        } if score else None,
    }


@router.get("/{wallet_id}/trades")
async def get_wallet_trades(
    wallet_id: UUID,
    limit: int = Query(100, le=500),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(WalletTransaction)
        .where(WalletTransaction.wallet_id == wallet_id)
        .order_by(WalletTransaction.detected_at.desc())
        .limit(limit)
    )
    txs = result.scalars().all()

    return {
        "trades": [
            {
                "id": str(t.id),
                "market_id": str(t.market_id),
                "side": t.side,
                "outcome": t.outcome,
                "price": float(t.price) if t.price else None,
                "size": float(t.size) if t.size else None,
                "notional": float(t.notional) if t.notional else None,
                "occurred_at": t.occurred_at.isoformat() if t.occurred_at else None,
                "detected_at": t.detected_at.isoformat() if t.detected_at else None,
                "detection_lag_ms": t.detection_lag_ms,
            }
            for t in txs
        ]
    }


@router.get("/{wallet_id}/performance")
async def get_wallet_performance(
    wallet_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Full realized copy performance for a wallet.

    Returns:
      - wallet_live_status: last_trade_at, activity_label, trades_24h
      - wallet_copy_performance: copied trades PnL breakdown per strategy
      - wallet_category_performance: per-category win rate / PnL from wallet transactions
      - wallet_influence_summary: leader/follower counts from influence graph
    """
    now = datetime.now(timezone.utc)

    # ── Live status ───────────────────────────────────────────────────────
    last_trade_row = await db.execute(
        select(func.max(WalletTransaction.occurred_at))
        .where(WalletTransaction.wallet_id == wallet_id)
    )
    last_trade_at = last_trade_row.scalar_one_or_none()

    trades_24h_row = await db.execute(
        select(func.count())
        .where(
            WalletTransaction.wallet_id == wallet_id,
            WalletTransaction.occurred_at >= now - timedelta(hours=24),
        )
    )
    trades_24h = trades_24h_row.scalar() or 0

    def _activity_label(last_at: datetime | None) -> str:
        if not last_at:
            return "UNKNOWN"
        last_at = last_at.replace(tzinfo=timezone.utc) if last_at.tzinfo is None else last_at
        delta = now - last_at
        if delta < timedelta(hours=24):
            return "ACTIVE"
        if delta < timedelta(days=7):
            return "WARM"
        return "DORMANT"

    live_status = {
        "last_trade_at": last_trade_at.isoformat() if last_trade_at else None,
        "trades_24h": int(trades_24h),
        "activity_label": _activity_label(last_trade_at),
    }

    # ── Copy performance (paper_positions breakdown) ───────────────────────
    pos_result = await db.execute(
        select(PaperPosition)
        .where(
            PaperPosition.source_wallet_id == wallet_id,
            PaperPosition.status == "closed",
        )
        .order_by(PaperPosition.opened_at.desc())
    )
    positions = pos_result.scalars().all()

    strategy_stats: dict[str, dict] = {}
    total_pnl = 0.0
    total_wins = 0

    for pos in positions:
        strat = pos.strategy or "unknown"
        if strat not in strategy_stats:
            strategy_stats[strat] = {"count": 0, "pnl": 0.0, "wins": 0, "losses": 0}
        pnl = float(pos.realized_pnl or 0)
        strategy_stats[strat]["count"] += 1
        strategy_stats[strat]["pnl"] += pnl
        if pnl > 0:
            strategy_stats[strat]["wins"] += 1
            total_wins += 1
        else:
            strategy_stats[strat]["losses"] += 1
        total_pnl += pnl

    total_count = len(positions)
    strategy_breakdown = []
    for strat, st in strategy_stats.items():
        cnt = st["count"]
        strategy_breakdown.append({
            "strategy": strat,
            "trade_count": cnt,
            "realized_pnl": round(st["pnl"], 4),
            "win_rate": round(st["wins"] / cnt, 4) if cnt else None,
            "avg_pnl": round(st["pnl"] / cnt, 4) if cnt else None,
            "profit_factor": round(
                sum(float(p.realized_pnl or 0) for p in positions
                    if p.strategy == strat and float(p.realized_pnl or 0) > 0) /
                max(abs(sum(float(p.realized_pnl or 0) for p in positions
                    if p.strategy == strat and float(p.realized_pnl or 0) < 0)), 0.0001),
                4
            ),
        })

    # Last 10 closed trades for sparkline
    recent_closed = [
        {
            "opened_at": p.opened_at.isoformat() if p.opened_at else None,
            "pnl": float(p.realized_pnl or 0),
            "strategy": p.strategy,
        }
        for p in positions[:10]
    ]

    last_copied_at = positions[0].opened_at.isoformat() if positions else None
    copy_win_rate = (total_wins / total_count) if total_count > 0 else None
    avg_pnl = (total_pnl / total_count) if total_count > 0 else None
    gross_profit = sum(float(p.realized_pnl or 0) for p in positions if float(p.realized_pnl or 0) > 0)
    gross_loss = abs(sum(float(p.realized_pnl or 0) for p in positions if float(p.realized_pnl or 0) < 0))
    profit_factor = round(gross_profit / max(gross_loss, 0.0001), 4)

    copy_performance = {
        "copied_trade_count": total_count,
        "realized_pnl": round(total_pnl, 4),
        "win_rate": round(copy_win_rate, 4) if copy_win_rate is not None else None,
        "avg_pnl": round(avg_pnl, 4) if avg_pnl is not None else None,
        "profit_factor": profit_factor,
        "last_copied_at": last_copied_at,
        "strategy_breakdown": strategy_breakdown,
        "recent_closed": recent_closed,
    }

    # ── Category performance: wallet transactions + realized copy PnL ────────
    tx_with_market_q = await db.execute(
        select(WalletTransaction, Market.category)
        .join(Market, WalletTransaction.market_id == Market.id)
        .where(
            WalletTransaction.wallet_id == wallet_id,
            WalletTransaction.market_id.isnot(None),
        )
    )
    tx_rows = tx_with_market_q.all()

    cat_stats: dict[str, dict] = {}
    for tx, category in tx_rows:
        cat = category or "unknown"
        if cat not in cat_stats:
            cat_stats[cat] = {"count": 0, "wins": 0, "notional": 0.0, "edges": []}
        cat_stats[cat]["count"] += 1
        notional = float(tx.notional or 0)
        cat_stats[cat]["notional"] += notional
        # Estimate win from price (above 0.5 → win side)
        price = float(tx.price or 0.5)
        if tx.side == "BUY" and price < 0.7:
            cat_stats[cat]["wins"] += 1
        elif tx.side == "SELL" and price > 0.3:
            cat_stats[cat]["wins"] += 1

    # Realized copy PnL per category (join positions → markets)
    copy_cat_q = await db.execute(
        select(Market.category, PaperPosition.realized_pnl)
        .join(PaperPosition, PaperPosition.market_id == Market.id)
        .where(
            PaperPosition.source_wallet_id == wallet_id,
            PaperPosition.status == "closed",
        )
    )
    copy_cat_rows = copy_cat_q.all()

    copy_cat: dict[str, list[float]] = {}
    for cat_name, pnl in copy_cat_rows:
        c = cat_name or "unknown"
        copy_cat.setdefault(c, []).append(float(pnl or 0))

    category_performance = []
    all_cats = set(cat_stats.keys()) | set(copy_cat.keys())
    for cat in sorted(all_cats, key=lambda x: -(cat_stats.get(x, {}).get("count", 0))):
        cst = cat_stats.get(cat, {})
        cnt = cst.get("count", 0)
        copy_pnls = copy_cat.get(cat, [])
        copy_cnt = len(copy_pnls)
        copy_total = sum(copy_pnls)
        copy_wins = sum(1 for p in copy_pnls if p > 0)
        copy_pf: float | None = None
        if copy_pnls:
            gp = sum(p for p in copy_pnls if p > 0)
            gl = abs(sum(p for p in copy_pnls if p < 0))
            copy_pf = round(gp / max(gl, 0.0001), 3)
        category_performance.append({
            "category": cat,
            "trade_count": cnt,
            "total_notional": round(cst.get("notional", 0), 2),
            "est_win_rate": round(cst["wins"] / cnt, 4) if cnt > 0 else None,
            "copied_trade_count": copy_cnt,
            "copied_realized_pnl": round(copy_total, 4) if copy_cnt > 0 else None,
            "copied_win_rate": round(copy_wins / copy_cnt, 4) if copy_cnt > 0 else None,
            "copied_profit_factor": copy_pf,
        })

    # ── Influence summary (lightweight — full graph is expensive) ──────────
    influence_summary = {"is_leader": False, "is_follower": False, "leader_count": 0, "follower_count": 0,
                         "influence_score": None, "top_influenced": [], "top_influencing": [],
                         "note": "Use /api/wallets/intelligence/influence-graph for full graph"}

    return {
        "wallet_id": str(wallet_id),
        "live_status": live_status,
        "copy_performance": copy_performance,
        "category_performance": category_performance,
        "influence_summary": influence_summary,
    }


# ── Wallet Alpha Intelligence Endpoints ──────────────────────────────────

@router.get("/{wallet_id}/alpha")
async def get_wallet_alpha(
    wallet_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Full copyable_alpha_score for a wallet.

    Returns 6-factor breakdown:
      timing_score, persistence_score, vol_adjusted_roi,
      latency_survivability, drawdown_stability, information_impact

    Plus: alpha_decay_risk, recommendation (copy_now / monitor / avoid),
    timing detail, influence detail.
    """
    from app.intelligence.wallet_alpha import compute_copyable_alpha

    result = await compute_copyable_alpha(db, wallet_id)

    return {
        "wallet_id": str(wallet_id),
        "copyable_alpha_score": result.copyable_alpha_score,
        "recommendation": result.recommendation,
        "alpha_decay_risk": result.alpha_decay_risk,
        "decay_signal": result.decay_signal,
        "factors": {
            "timing_score": result.timing_score,
            "persistence_score": result.persistence_score,
            "vol_adjusted_roi": result.vol_adjusted_roi,
            "latency_survivability": result.latency_survivability,
            "drawdown_stability": result.drawdown_stability,
            "information_impact": result.information_impact,
            "suspiciousness_penalty": result.suspiciousness_penalty,
        },
        "timing_detail": result.timing_detail,
        "influence_detail": result.influence_detail,
    }


@router.get("/{wallet_id}/alpha-decay")
async def get_alpha_decay(
    wallet_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Alpha decay monitor for a wallet.

    Compares rolling PF across 10 / 20 / 40 trade windows.
    Returns decay_alert (bool) + magnitude + per-window metrics.

    If decay_alert=true, reduce position size or pause copying.
    """
    from app.intelligence.wallet_alpha import detect_alpha_decay
    return await detect_alpha_decay(db, wallet_id)


@router.get("/intelligence/influence-graph")
async def get_influence_graph(
    max_lag_seconds: float = Query(600, ge=30, le=3600),
    min_observations: int = Query(2, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Wallet influence graph — information leaders vs followers.

    Algorithm:
      - Groups trades by market
      - Detects when wallet A consistently trades before wallet B
      - Identifies leaders (high outgoing influence, low incoming)
      - Returns edge list with lag, correlation, and weight

    Use this to:
      - Prioritise copying leaders over followers
      - Detect copy-cat wallets that add no independent signal
      - Understand information flow in the market
    """
    from app.intelligence.wallet_alpha import build_influence_graph

    graph = await build_influence_graph(
        db,
        max_lag_seconds=max_lag_seconds,
        min_observations=min_observations,
    )

    return {
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "leader_count": len(graph.leaders),
        "follower_count": len(graph.followers),
        "leaders": graph.leaders[:10],   # top 10 for UI
        "followers": graph.followers[:10],
        "top_edges": [
            {
                "leader": e.leader_wallet_id[:12] + "...",
                "follower": e.follower_wallet_id[:12] + "...",
                "market_id": e.market_id,
                "mean_lag_seconds": e.mean_lag_seconds,
                "min_lag_seconds": e.min_lag_seconds,
                "max_lag_seconds": e.max_lag_seconds,
                "trade_count": e.trade_count,
                "outcome_correlation": e.outcome_correlation,
                "avg_price_move": e.avg_price_move,
                "weight": e.weight,
            }
            for e in sorted(graph.edges, key=lambda x: -x.weight)[:20]
        ],
        "influence_scores": dict(
            sorted(graph.influence_scores.items(), key=lambda x: -x[1])[:10]
        ),
        "computed_at": graph.computed_at.isoformat(),
        "params": {
            "max_lag_seconds": max_lag_seconds,
            "min_observations": min_observations,
        },
    }


@router.get("/intelligence/alpha-leaderboard")
async def get_intelligence_alpha_leaderboard(
    limit: int = Query(20, le=50),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Leaderboard ranked by copyable_alpha_score (6-factor engine).
      - alpha_decay_risk
      - top 3 factor scores

    Fast path: uses WalletScore fields directly for top wallets
    (composite_score * copyability_score as proxy), then computes
    full alpha only for the top candidates.
    """
    from app.intelligence.wallet_alpha import compute_copyable_alpha

    # Fast pre-filter: only compute full alpha for top N by composite*copyability
    subq = (
        select(
            WalletScore.wallet_id,
            func.max(WalletScore.scored_at).label("latest"),
        )
        .group_by(WalletScore.wallet_id)
        .subquery()
    )
    top_result = await db.execute(
        select(Wallet, WalletScore)
        .join(subq, Wallet.id == subq.c.wallet_id)
        .join(
            WalletScore,
            and_(
                WalletScore.wallet_id == subq.c.wallet_id,
                WalletScore.scored_at == subq.c.latest,
            ),
        )
        .where(Wallet.is_tracked.is_(True))
        .order_by((WalletScore.copyability_score * WalletScore.composite_score).desc())
        .limit(min(limit * 2, 40))   # cap at 40 to avoid N+1 timeout
    )
    top_rows = top_result.all()

    scores = []
    for w, s in top_rows:
        try:
            alpha = await compute_copyable_alpha(db, w.id)
            scores.append({
                "wallet_id": str(w.id),
                "address": w.address[:12] + "...",
                "label": w.label,
                "copyable_alpha_score": alpha.copyable_alpha_score,
                "recommendation": alpha.recommendation,
                "alpha_decay_risk": alpha.alpha_decay_risk,
                "decay_signal": alpha.decay_signal,
                "composite_score": float(s.composite_score or 0),
                "copyability_score": float(s.copyability_score or 0),
                "top_factors": {
                    "timing": alpha.timing_score,
                    "persistence": alpha.persistence_score,
                    "latency_survivability": alpha.latency_survivability,
                    "information_impact": alpha.information_impact,
                },
            })
        except Exception:
            continue

    scores.sort(key=lambda x: -x["copyable_alpha_score"])

    return {
        "leaderboard": scores[:limit],
        "total_evaluated": len(scores),
        "copy_now_count": sum(1 for s in scores if s["recommendation"] == "copy_now"),
        "monitor_count": sum(1 for s in scores if s["recommendation"] == "monitor"),
        "avoid_count": sum(1 for s in scores if s["recommendation"] == "avoid"),
    }


@router.get("/intelligence/leader-impact")
async def get_leader_impact_leaderboard(
    min_score: float = Query(0.50, description="Minimum wallet composite score"),
    top_n: int = Query(20, le=50),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Information Propagation Leaderboard.

    Ranks wallets by measured price impact after their trades:
      avg_aligned_move_60s  — direction-adjusted price move 60s after trade
      hit_rate_60s          — % of trades where price moved in leader's direction
      prop_signal           — composite propagation signal (0–1)

    This is the core of the information propagation strategy:
    wallets with high prop_signal = real information leaders.
    """
    from app.intelligence.leader_impact import get_leader_impact_leaderboard

    leaders = await get_leader_impact_leaderboard(db, min_score=min_score, top_n=top_n)

    if not leaders:
        return {
            "leaders": [],
            "note": (
                "No leaders found yet. Need wallet_transactions with market_snapshots "
                "at the same timestamps. Data accumulates over time."
            ),
            "data_requirement": (
                "leader_impact requires: wallet trade + market snapshot within 90s after trade. "
                f"Currently tracking {min_score:.0%}+ score wallets."
            ),
        }

    # Classify leaders
    for w in leaders:
        move = w.get("avg_aligned_move_60s") or 0
        hit  = w.get("hit_rate_60s") or 0
        sig  = w.get("prop_signal") or 0
        if sig >= 0.3 and hit >= 0.65:
            w["classification"] = "strong_leader"
        elif sig >= 0.15 and hit >= 0.55:
            w["classification"] = "moderate_leader"
        elif move > 0 and hit > 0.5:
            w["classification"] = "weak_leader"
        else:
            w["classification"] = "no_impact"

    return {
        "leaders": leaders,
        "strong_leaders": sum(1 for w in leaders if w["classification"] == "strong_leader"),
        "total": len(leaders),
        "note": (
            "prop_signal = impact × size × liquidity × time_decay. "
            "Strong leaders (>0.3) are candidates for information propagation strategy. "
            "avg_aligned_move_60s > 0.02 = meaningful information edge."
        ),
    }


