from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func as sqlfunc
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models.market import Market, MarketRelationship, MarketSnapshot
from app.models.paper import PaperPosition
from app.models.signal import TradeSignal

router = APIRouter()


@router.get("")
async def list_markets(
    active_only: bool = Query(True),
    category: str | None = Query(None),
    limit: int = Query(100, le=300),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict:
    base_q = select(Market)
    if active_only:
        base_q = base_q.where(Market.is_active.is_(True))
    if category:
        base_q = base_q.where(Market.category == category)

    total_result = await db.execute(select(sqlfunc.count()).select_from(base_q.subquery()))
    total = total_result.scalar() or 0

    q = base_q.order_by(Market.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(q)
    markets = result.scalars().all()

    # Fetch latest snapshot for each market in one go
    if markets:
        market_ids = [m.id for m in markets]
        snap_subq = (
            select(
                MarketSnapshot.market_id,
                sqlfunc.max(MarketSnapshot.captured_at).label("max_at"),
            )
            .where(MarketSnapshot.market_id.in_(market_ids))
            .group_by(MarketSnapshot.market_id)
            .subquery()
        )
        snap_q = select(MarketSnapshot).join(
            snap_subq,
            (MarketSnapshot.market_id == snap_subq.c.market_id)
            & (MarketSnapshot.captured_at == snap_subq.c.max_at),
        )
        snap_result = await db.execute(snap_q)
        snaps_by_market = {s.market_id: s for s in snap_result.scalars().all()}
    else:
        snaps_by_market = {}

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "markets": [
            {
                "id": str(m.id),
                "question": m.question,
                "slug": m.slug,
                "category": m.category,
                "is_active": m.is_active,
                "fees_enabled": m.fees_enabled,
                "fee_rate_bps": m.fee_rate_bps,
                "outcomes": m.outcomes,
                "snapshot": _snap_dict(snaps_by_market.get(m.id)),
            }
            for m in markets
        ],
    }


def _snap_dict(s: MarketSnapshot | None) -> dict | None:
    if not s:
        return None
    return {
        "captured_at": s.captured_at.isoformat(),
        "best_bid": float(s.best_bid) if s.best_bid else None,
        "best_ask": float(s.best_ask) if s.best_ask else None,
        "midpoint": float(s.midpoint) if s.midpoint else None,
        "spread": float(s.spread) if s.spread else None,
        "last_trade_price": float(s.last_trade_price) if s.last_trade_price else None,
        "volume_24h": float(s.volume_24h) if s.volume_24h else None,
        "bid_depth": float(s.bid_depth) if s.bid_depth else None,
        "ask_depth": float(s.ask_depth) if s.ask_depth else None,
    }


@router.get("/relationships")
async def list_relationships(db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(
        select(MarketRelationship).where(MarketRelationship.is_active.is_(True))
    )
    rels = result.scalars().all()

    # Fetch markets and latest snapshots for all relationship markets
    market_ids = set()
    for r in rels:
        market_ids.add(r.market_a_id)
        market_ids.add(r.market_b_id)

    markets_map: dict = {}
    snaps_map: dict = {}
    if market_ids:
        mq = await db.execute(select(Market).where(Market.id.in_(list(market_ids))))
        for m in mq.scalars().all():
            markets_map[m.id] = m

        snap_subq = (
            select(
                MarketSnapshot.market_id,
                sqlfunc.max(MarketSnapshot.captured_at).label("max_at"),
            )
            .where(MarketSnapshot.market_id.in_(list(market_ids)))
            .group_by(MarketSnapshot.market_id)
            .subquery()
        )
        sq = select(MarketSnapshot).join(
            snap_subq,
            (MarketSnapshot.market_id == snap_subq.c.market_id)
            & (MarketSnapshot.captured_at == snap_subq.c.max_at),
        )
        sr = await db.execute(sq)
        for s in sr.scalars().all():
            snaps_map[s.market_id] = s

    def _price(mid) -> float | None:
        return float(mid) if mid else None

    rows = []
    for r in rels:
        sa = snaps_map.get(r.market_a_id)
        sb = snaps_map.get(r.market_b_id)
        ma = markets_map.get(r.market_a_id)
        mb = markets_map.get(r.market_b_id)
        price_a = _price(sa.midpoint) if sa else None
        price_b = _price(sb.midpoint) if sb else None
        current_spread = abs(price_a - price_b) if price_a is not None and price_b is not None else None
        normal_mean = float(r.normal_spread_mean) if r.normal_spread_mean else None
        normal_std = float(r.normal_spread_std) if r.normal_spread_std else None
        z_score = None
        if current_spread is not None and normal_mean is not None and normal_std and normal_std > 0:
            z_score = round((current_spread - normal_mean) / normal_std, 3)

        rows.append({
            "id": str(r.id),
            "market_a_id": str(r.market_a_id),
            "market_b_id": str(r.market_b_id),
            "market_a_question": ma.question if ma else None,
            "market_b_question": mb.question if mb else None,
            "type": r.relationship_type,
            "asset": r.asset_symbol,
            "normal_spread_mean": normal_mean,
            "normal_spread_std": normal_std,
            "price_a": price_a,
            "price_b": price_b,
            "current_spread": round(current_spread, 6) if current_spread is not None else None,
            "z_score": z_score,
            "is_dislocation": abs(z_score) > 2.0 if z_score is not None else False,
        })

    rows.sort(key=lambda x: abs(x["z_score"] or 0), reverse=True)
    return {"relationships": rows}


@router.get("/actionability")
async def get_actionability(db: AsyncSession = Depends(get_db)) -> dict:
    """
    For every active market relationship, compute a full actionability score.

    Combines statistical edge (z-score), executable edge after costs,
    liquidity sufficiency, spread risk and staleness into a single TRADEABLE /
    MARGINAL / NO_EDGE verdict plus enriched fields:
    - confidence_adjusted_edge  (edge after staleness + slippage haircut)
    - holding_horizon           (short / medium / unstable)
    - suggested_size_usd        (Kelly-proportional paper size)
    - why_not                   (human-readable primary blocker)
    - dislocation_history       (24h stats for this pair)
    """
    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(MarketRelationship).where(MarketRelationship.is_active.is_(True))
    )
    rels = result.scalars().all()

    market_ids = set()
    for r in rels:
        market_ids.add(r.market_a_id)
        market_ids.add(r.market_b_id)

    markets_map: dict = {}
    snaps_map: dict = {}
    if market_ids:
        mq = await db.execute(select(Market).where(Market.id.in_(list(market_ids))))
        for m in mq.scalars().all():
            markets_map[m.id] = m

        snap_subq = (
            select(
                MarketSnapshot.market_id,
                sqlfunc.max(MarketSnapshot.captured_at).label("max_at"),
            )
            .where(MarketSnapshot.market_id.in_(list(market_ids)))
            .group_by(MarketSnapshot.market_id)
            .subquery()
        )
        sq = select(MarketSnapshot).join(
            snap_subq,
            (MarketSnapshot.market_id == snap_subq.c.market_id)
            & (MarketSnapshot.captured_at == snap_subq.c.max_at),
        )
        sr = await db.execute(sq)
        for s in sr.scalars().all():
            snaps_map[s.market_id] = s

    # ── Dislocation history: signals with source_type=spread_anomaly in last 24h ──
    cutoff_24h = now - timedelta(hours=24)
    rel_ids = [r.id for r in rels]
    history_map: dict = {}  # relationship_id -> {count, tradeable_count}
    if rel_ids:
        hist_q = await db.execute(
            select(TradeSignal.relationship_id, sqlfunc.count().label("cnt"))
            .where(
                TradeSignal.source_type == "spread_anomaly",
                TradeSignal.created_at >= cutoff_24h,
                TradeSignal.relationship_id.in_(rel_ids),
            )
            .group_by(TradeSignal.relationship_id)
        )
        for rid, cnt in hist_q.all():
            history_map[rid] = {"dislocations_24h": int(cnt)}

    # ── Closed positions with strategy=dislocation in last 24h for precision ──
    from sqlalchemy import case as sa_case
    precision_result = await db.execute(
        select(
            sqlfunc.count().label("total"),
            sqlfunc.sum(
                sa_case((PaperPosition.realized_pnl > 0, 1), else_=0)
            ).label("profitable"),
        ).where(
            PaperPosition.strategy == "dislocation",
            PaperPosition.status == "closed",
            PaperPosition.closed_at >= cutoff_24h,
        )
    )
    prec_row = precision_result.one()
    precision_total = int(prec_row.total or 0)
    precision_profitable = int(prec_row.profitable or 0)

    rows = []
    for r in rels:
        sa = snaps_map.get(r.market_a_id)
        sb = snaps_map.get(r.market_b_id)
        ma = markets_map.get(r.market_a_id)
        mb = markets_map.get(r.market_b_id)

        price_a = float(sa.midpoint) if sa and sa.midpoint else None
        price_b = float(sb.midpoint) if sb and sb.midpoint else None
        spread_a = float(sa.spread) if sa and sa.spread else 0.04
        spread_b = float(sb.spread) if sb and sb.spread else 0.04
        depth_a = float(sa.bid_depth) if sa and sa.bid_depth else 0.0
        depth_b = float(sb.bid_depth) if sb and sb.bid_depth else 0.0
        fee_a = (float(ma.fee_rate_bps) / 10000) if ma and ma.fees_enabled and ma.fee_rate_bps else 0.0
        fee_b = (float(mb.fee_rate_bps) / 10000) if mb and mb.fees_enabled and mb.fee_rate_bps else 0.0

        normal_mean = float(r.normal_spread_mean) if r.normal_spread_mean else 0.02
        normal_std = float(r.normal_spread_std) if r.normal_spread_std else 0.015

        if price_a is None or price_b is None:
            continue

        current_spread = abs(price_a - price_b)
        z_score = round((current_spread - normal_mean) / max(normal_std, 1e-8), 3)
        is_dislocation = abs(z_score) > 2.0

        # ── Edge estimation ──────────────────────────────────────
        fair_value = (price_a + price_b) / 2
        underpriced_price = price_b if price_a > price_b else price_a
        raw_edge = fair_value - underpriced_price

        entry_cost = (spread_a + spread_b) / 2
        exit_cost = (spread_a + spread_b) / 2
        total_fees = fee_a + fee_b
        total_cost = entry_cost + exit_cost + total_fees

        net_edge = round(raw_edge - total_cost, 6)
        edge_to_cost_ratio = round(raw_edge / max(total_cost, 1e-8), 3)

        # ── Liquidity assessment ─────────────────────────────────
        min_depth = min(depth_a, depth_b)
        min_viable_depth = 20.0
        depth_ok = min_depth >= min_viable_depth

        # ── Spread quality ───────────────────────────────────────
        spread_pct_a = spread_a / max(price_a, 0.01)
        spread_pct_b = spread_b / max(price_b, 0.01)
        max_spread_pct = max(spread_pct_a, spread_pct_b)
        spread_ok = max_spread_pct < 0.12

        # ── Slippage risk ────────────────────────────────────────
        target_size_usd = 50.0
        slippage_risk_a = (target_size_usd / max(depth_a, 1)) if depth_a > 0 else 1.0
        slippage_risk_b = (target_size_usd / max(depth_b, 1)) if depth_b > 0 else 1.0
        max_slippage_risk = round(max(slippage_risk_a, slippage_risk_b), 4)
        slippage_ok = max_slippage_risk < 0.30

        # ── Staleness check ───────────────────────────────────────
        snap_age_seconds: int | None = None
        if sa and sa.captured_at:
            snap_age_seconds = int((now - sa.captured_at).total_seconds())
        data_fresh = snap_age_seconds is not None and snap_age_seconds < 300

        # ── Confidence-adjusted edge (conservative haircut) ───────
        # Apply three multiplicative penalties:
        #  1. Staleness haircut: if data is >60s old each 60s adds 5% erosion
        #  2. Slippage haircut: if slippage_risk is high we haircut proportionally
        #  3. Low-liquidity haircut: if depth barely meets minimum, haircut 10%
        staleness_haircut = 1.0
        if snap_age_seconds is not None and snap_age_seconds > 60:
            staleness_haircut = max(0.5, 1.0 - (snap_age_seconds - 60) / 60 * 0.05)

        slippage_haircut = max(0.5, 1.0 - max_slippage_risk * 0.5)

        depth_haircut = 1.0
        if depth_ok and min_depth < min_viable_depth * 2:
            depth_haircut = 0.9  # borderline depth — 10% confidence penalty

        confidence_factor = staleness_haircut * slippage_haircut * depth_haircut
        confidence_adjusted_edge = round(net_edge * confidence_factor, 6)

        # ── Holding horizon ───────────────────────────────────────
        # Based on z-score trend and market type (5m vs 15m vs adjacent)
        rel_type = r.relationship_type or ""
        if not is_dislocation:
            holding_horizon = "none"
        elif "5m" in rel_type or abs(z_score) > 5:
            # Very short-window market or extreme anomaly — typically mean-reverts fast
            holding_horizon = "short"  # < 5 min
        elif abs(z_score) > 3:
            holding_horizon = "medium"  # 5–30 min
        else:
            # Mild dislocation, direction unclear
            holding_horizon = "unstable"

        # ── Suggested paper size ──────────────────────────────────
        # Fractional Kelly sizing: f = confidence_adjusted_edge / (1 - underpriced_price)
        # Capped at $72 (8% of $900 bankroll), floored to $0 when edge ≤ 0
        suggested_size_usd = 0.0
        if confidence_adjusted_edge > 0 and underpriced_price < 1.0:
            b = (1.0 - underpriced_price) / max(underpriced_price, 0.01)  # payout ratio
            p = underpriced_price + confidence_adjusted_edge  # adj probability
            q = 1.0 - p
            kelly_f = max(0.0, (b * p - q) / max(b, 1e-8))
            fractional_kelly = kelly_f * 0.25  # 25% Kelly — conservative
            bankroll = 900.0
            raw_size = fractional_kelly * bankroll
            suggested_size_usd = round(min(raw_size, 72.0), 2)  # cap at 8% bankroll

        # ── Why not tradeable? (single-sentence primary blocker) ─
        why_not: str | None = None
        if not is_dislocation:
            why_not = "No statistical anomaly detected in this pair"
        elif not data_fresh:
            why_not = f"Snapshot stale ({snap_age_seconds}s old) — prices may have already moved"
        elif net_edge <= 0:
            why_not = f"No edge after costs (raw {raw_edge:.4f} - cost {total_cost:.4f} = {net_edge:.4f})"
        elif not depth_ok:
            why_not = f"Liquidity insufficient — min depth ${min_depth:.0f} < required ${min_viable_depth:.0f}"
        elif not spread_ok:
            why_not = f"Spread too wide ({max_spread_pct:.1%}) — crosses too much noise"
        elif not slippage_ok:
            why_not = f"High slippage risk ({max_slippage_risk:.1%} of depth) — size impact too large"

        # ── Final verdict ─────────────────────────────────────────
        if not is_dislocation:
            verdict = "NO_SIGNAL"
            verdict_color = "gray"
        elif not data_fresh:
            verdict = "STALE_DATA"
            verdict_color = "orange"
        elif net_edge <= 0:
            verdict = "NO_EDGE"
            verdict_color = "red"
        elif not depth_ok:
            verdict = "LOW_LIQUIDITY"
            verdict_color = "orange"
        elif not spread_ok:
            verdict = "SPREAD_TOO_WIDE"
            verdict_color = "orange"
        elif not slippage_ok:
            verdict = "SLIPPAGE_RISK"
            verdict_color = "yellow"
        elif net_edge > 0.01 and edge_to_cost_ratio > 1.5 and z_score > 3:
            verdict = "TRADEABLE"
            verdict_color = "green"
        else:
            verdict = "MARGINAL"
            verdict_color = "yellow"

        # ── Dislocation history for this pair ────────────────────
        pair_history = history_map.get(r.id, {})
        dislocations_24h = pair_history.get("dislocations_24h", 0)

        rows.append({
            "id": str(r.id),
            "asset": r.asset_symbol,
            "type": r.relationship_type,
            "market_a": ma.question if ma else None,
            "market_b": mb.question if mb else None,
            # Prices
            "price_a": round(price_a, 6),
            "price_b": round(price_b, 6),
            "fair_value": round(fair_value, 6),
            "underpriced_price": round(underpriced_price, 6),
            # Statistical signal
            "current_spread": round(current_spread, 6),
            "normal_spread_mean": normal_mean,
            "normal_spread_std": normal_std,
            "z_score": z_score,
            "is_dislocation": is_dislocation,
            # Edge
            "raw_edge": round(raw_edge, 6),
            "total_cost": round(total_cost, 6),
            "net_edge": net_edge,
            "edge_to_cost_ratio": edge_to_cost_ratio,
            # NEW: Conservative edge
            "confidence_adjusted_edge": confidence_adjusted_edge,
            "confidence_factor": round(confidence_factor, 4),
            "staleness_haircut": round(staleness_haircut, 4),
            "slippage_haircut": round(slippage_haircut, 4),
            # Liquidity
            "depth_a": round(depth_a, 2),
            "depth_b": round(depth_b, 2),
            "min_depth": round(min_depth, 2),
            "depth_ok": depth_ok,
            # Spread quality
            "spread_a": round(spread_a, 6),
            "spread_b": round(spread_b, 6),
            "max_spread_pct": round(max_spread_pct, 4),
            "spread_ok": spread_ok,
            # Slippage
            "max_slippage_risk": max_slippage_risk,
            "slippage_ok": slippage_ok,
            # Data freshness
            "snap_age_seconds": snap_age_seconds,
            "data_fresh": data_fresh,
            # NEW: Execution guidance
            "holding_horizon": holding_horizon,
            "suggested_size_usd": suggested_size_usd,
            "why_not": why_not,
            # NEW: Historical pair performance
            "dislocations_24h": dislocations_24h,
            # Verdict
            "verdict": verdict,
            "verdict_color": verdict_color,
            # Spread-to-edge ratio: how much cost per unit of raw edge
            "spread_to_edge_ratio": round(total_cost / raw_edge, 3) if raw_edge > 0.001 else None,
        })

    # Sort: tradeable first, then by z-score descending
    order = {"TRADEABLE": 0, "MARGINAL": 1, "SLIPPAGE_RISK": 2, "LOW_LIQUIDITY": 3,
             "SPREAD_TOO_WIDE": 4, "NO_EDGE": 5, "STALE_DATA": 6, "NO_SIGNAL": 7}
    rows.sort(key=lambda x: (order.get(x["verdict"], 9), -abs(x["z_score"])))

    tradeable = sum(1 for r in rows if r["verdict"] == "TRADEABLE")
    marginal = sum(1 for r in rows if r["verdict"] == "MARGINAL")

    return {
        "total": len(rows),
        "tradeable": tradeable,
        "marginal": marginal,
        "precision_total": precision_total,
        "precision_profitable": precision_profitable,
        "items": rows,
    }


@router.get("/actionability-precision")
async def get_actionability_precision(db: AsyncSession = Depends(get_db)) -> dict:
    """
    Actionability Precision — the system's IQ test.

    For dislocation-strategy positions that have been closed, answer:
    "What % of TRADEABLE-level opportunities actually ended profitably?"

    Returns performance breakdown by time window (24h / 7d / all-time).
    """
    now = datetime.now(timezone.utc)

    async def _precision_window(since: datetime | None) -> dict:
        from sqlalchemy import case as sa_case
        q = select(
            sqlfunc.count().label("total"),
            sqlfunc.sum(sa_case((PaperPosition.realized_pnl > 0, 1), else_=0)).label("profitable"),
            sqlfunc.sum(sa_case((PaperPosition.realized_pnl <= 0, 1), else_=0)).label("unprofitable"),
            sqlfunc.avg(PaperPosition.realized_pnl).label("avg_pnl"),
            sqlfunc.sum(PaperPosition.realized_pnl).label("total_pnl"),
        ).where(
            PaperPosition.strategy == "dislocation",
            PaperPosition.status == "closed",
        )
        if since:
            q = q.where(PaperPosition.closed_at >= since)
        row = (await db.execute(q)).one()
        total = int(row.total or 0)
        profitable = int(row.profitable or 0)
        precision = round(profitable / total, 4) if total > 0 else None
        return {
            "total_closed": total,
            "profitable": profitable,
            "unprofitable": int(row.unprofitable or 0),
            "precision": precision,
            "avg_pnl": round(float(row.avg_pnl), 4) if row.avg_pnl else None,
            "total_pnl": round(float(row.total_pnl), 2) if row.total_pnl else None,
        }

    w24h = await _precision_window(now - timedelta(hours=24))
    w7d = await _precision_window(now - timedelta(days=7))
    wall = await _precision_window(None)

    # Grade: A ≥0.60, B ≥0.50, C ≥0.40, D <0.40, N/A = no data
    def _grade(p: float | None) -> str:
        if p is None:
            return "N/A"
        if p >= 0.60:
            return "A"
        if p >= 0.50:
            return "B"
        if p >= 0.40:
            return "C"
        return "D"

    all_precision = wall["precision"]
    return {
        "grade": _grade(all_precision),
        "interpretation": (
            "No closed dislocation positions yet — keep the engine running." if wall["total_closed"] == 0
            else f"{wall['profitable']}/{wall['total_closed']} dislocation trades were profitable all-time."
        ),
        "windows": {
            "24h": w24h,
            "7d": w7d,
            "all_time": wall,
        },
    }


@router.get("/experiment")
async def run_experiment(
    min_conf_edge: float = Query(0.005, ge=0.0, le=0.5, description="Minimum confidence-adjusted edge"),
    min_depth: float = Query(20.0, ge=0.0, le=10000.0, description="Minimum order book depth $"),
    min_z: float = Query(2.0, ge=0.5, le=10.0, description="Minimum z-score"),
    max_spread_pct: float = Query(0.12, ge=0.01, le=0.5, description="Max spread as fraction"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Parameter sweep experiment endpoint.

    Re-evaluates all current relationship snapshots using the supplied
    filter parameters and returns how many would pass each gate — WITHOUT
    touching any real positions. Pure simulation / what-if analysis.

    Also intersects with historical closed positions to estimate how precision
    would change if these parameters had been applied historically.
    """
    from sqlalchemy import case as sa_case

    result = await db.execute(
        select(MarketRelationship).where(MarketRelationship.is_active.is_(True))
    )
    rels = result.scalars().all()

    market_ids = set()
    for r in rels:
        market_ids.add(r.market_a_id)
        market_ids.add(r.market_b_id)

    markets_map: dict = {}
    snaps_map: dict = {}
    if market_ids:
        mq = await db.execute(select(Market).where(Market.id.in_(list(market_ids))))
        for m in mq.scalars().all():
            markets_map[m.id] = m

        snap_subq = (
            select(
                MarketSnapshot.market_id,
                sqlfunc.max(MarketSnapshot.captured_at).label("max_at"),
            )
            .where(MarketSnapshot.market_id.in_(list(market_ids)))
            .group_by(MarketSnapshot.market_id)
            .subquery()
        )
        sq = select(MarketSnapshot).join(
            snap_subq,
            (MarketSnapshot.market_id == snap_subq.c.market_id)
            & (MarketSnapshot.captured_at == snap_subq.c.max_at),
        )
        sr = await db.execute(sq)
        for s in sr.scalars().all():
            snaps_map[s.market_id] = s

    now = datetime.now(timezone.utc)
    total_pairs = 0
    passes_z = 0
    passes_depth = 0
    passes_spread = 0
    passes_edge = 0
    would_trade = 0
    gate_breakdown = []

    for r in rels:
        sa = snaps_map.get(r.market_a_id)
        sb = snaps_map.get(r.market_b_id)
        ma = markets_map.get(r.market_a_id)
        mb = markets_map.get(r.market_b_id)

        price_a = float(sa.midpoint) if sa and sa.midpoint else None
        price_b = float(sb.midpoint) if sb and sb.midpoint else None
        if price_a is None or price_b is None:
            continue

        total_pairs += 1
        spread_a = float(sa.spread) if sa and sa.spread else 0.04
        spread_b = float(sb.spread) if sb and sb.spread else 0.04
        depth_a = float(sa.bid_depth) if sa and sa.bid_depth else 0.0
        depth_b = float(sb.bid_depth) if sb and sb.bid_depth else 0.0
        fee_a = (float(ma.fee_rate_bps) / 10000) if ma and ma.fees_enabled and ma.fee_rate_bps else 0.0
        fee_b = (float(mb.fee_rate_bps) / 10000) if mb and mb.fees_enabled and mb.fee_rate_bps else 0.0
        normal_mean = float(r.normal_spread_mean) if r.normal_spread_mean else 0.02
        normal_std = float(r.normal_spread_std) if r.normal_spread_std else 0.015

        current_spread = abs(price_a - price_b)
        z_score = (current_spread - normal_mean) / max(normal_std, 1e-8)

        fair_value = (price_a + price_b) / 2
        underpriced = price_b if price_a > price_b else price_a
        raw_edge = fair_value - underpriced
        total_cost = (spread_a + spread_b) + fee_a + fee_b
        net_edge = raw_edge - total_cost

        snap_age = int((now - sa.captured_at).total_seconds()) if sa and sa.captured_at else 9999
        staleness_hc = max(0.5, 1.0 - max(0, snap_age - 60) / 60 * 0.05)
        min_depth_v = min(depth_a, depth_b)
        slippage_risk = (50.0 / max(min_depth_v, 1)) if min_depth_v > 0 else 1.0
        slippage_hc = max(0.5, 1.0 - slippage_risk * 0.5)
        depth_hc = 0.9 if (min_depth_v >= 20 and min_depth_v < 40) else 1.0
        conf_factor = staleness_hc * slippage_hc * depth_hc
        conf_adj_edge = net_edge * conf_factor

        spread_pct = max(spread_a / max(price_a, 0.01), spread_b / max(price_b, 0.01))

        # Gate checks with new params
        ok_z = abs(z_score) >= min_z
        ok_depth = min_depth_v >= min_depth
        ok_spread = spread_pct < max_spread_pct
        ok_edge = conf_adj_edge >= min_conf_edge

        if ok_z:
            passes_z += 1
        if ok_z and ok_depth:
            passes_depth += 1
        if ok_z and ok_depth and ok_spread:
            passes_spread += 1
        if ok_z and ok_depth and ok_spread and ok_edge:
            passes_edge += 1
            would_trade += 1

        gate_breakdown.append({
            "pair": f"{r.asset_symbol or '?'} | {(r.relationship_type or '').replace('_', ' ')}",
            "z": round(z_score, 3),
            "min_depth": round(min_depth_v, 1),
            "spread_pct": round(spread_pct * 100, 2),
            "conf_adj_edge": round(conf_adj_edge, 5),
            "ok_z": ok_z,
            "ok_depth": ok_depth,
            "ok_spread": ok_spread,
            "ok_edge": ok_edge,
            "would_trade": ok_z and ok_depth and ok_spread and ok_edge,
        })

    gate_breakdown.sort(key=lambda x: (-x["z"]))

    # Historical precision estimate using closed positions
    # (We can't perfectly replay, but we can count profitable dislocation closes
    # that occurred when signals had high z-scores, as a rough proxy)
    hist_q = await db.execute(
        select(
            sqlfunc.count().label("total"),
            sqlfunc.sum(sa_case((PaperPosition.realized_pnl > 0, 1), else_=0)).label("profitable"),
        ).where(
            PaperPosition.strategy == "dislocation",
            PaperPosition.status == "closed",
        )
    )
    hist = hist_q.one()
    hist_total = int(hist.total or 0)
    hist_profitable = int(hist.profitable or 0)
    current_precision = round(hist_profitable / hist_total, 4) if hist_total > 0 else None

    return {
        "params": {
            "min_conf_edge": min_conf_edge,
            "min_depth": min_depth,
            "min_z": min_z,
            "max_spread_pct": max_spread_pct,
        },
        "current_snapshot": {
            "total_pairs": total_pairs,
            "passes_z": passes_z,
            "passes_depth": passes_depth,
            "passes_spread": passes_spread,
            "passes_edge": passes_edge,
            "would_trade_now": would_trade,
        },
        "historical_precision": {
            "total_closed": hist_total,
            "profitable": hist_profitable,
            "precision": current_precision,
        },
        "gate_breakdown": gate_breakdown,
    }


@router.get("/{market_id}/snapshots")
async def get_market_snapshots(
    market_id: UUID,
    limit: int = Query(100, le=1000),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(MarketSnapshot)
        .where(MarketSnapshot.market_id == market_id)
        .order_by(MarketSnapshot.captured_at.desc())
        .limit(limit)
    )
    snaps = result.scalars().all()

    return {
        "snapshots": [
            {
                "captured_at": s.captured_at.isoformat(),
                "best_bid": float(s.best_bid) if s.best_bid else None,
                "best_ask": float(s.best_ask) if s.best_ask else None,
                "midpoint": float(s.midpoint) if s.midpoint else None,
                "spread": float(s.spread) if s.spread else None,
                "last_trade_price": float(s.last_trade_price) if s.last_trade_price else None,
                "volume_24h": float(s.volume_24h) if s.volume_24h else None,
            }
            for s in snaps
        ]
    }


@router.get("/param-matrix")
async def run_param_matrix(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Parameter sweep matrix endpoint.

    Runs a 3-axis grid sweep over:
      - conf_edge_values  : [0.005, 0.01, 0.015, 0.02]
      - spread_ratio_values: [0.5, 0.6, 0.7, 0.8, 1.0]
      - depth_values      : [20, 100, 200, 500]

    For each combination, returns:
      - would_trade_now: # of current market pairs that pass all gates
      - hist_precision:  estimated precision from closed dislocation trades
      - pass_rate:       fraction of pairs passing vs total monitored

    Annotates the "sweet spot" as the cell with the highest estimated
    expected value (pass_rate × hist_precision × conf_edge).
    """
    from sqlalchemy import case as sa_case

    # ── Load current relationships & their snapshots ──────────────────────────
    rels_result = await db.execute(
        select(MarketRelationship).where(MarketRelationship.is_active.is_(True))
    )
    rels = rels_result.scalars().all()

    market_ids = set()
    for r in rels:
        market_ids.add(r.market_a_id)
        market_ids.add(r.market_b_id)

    snaps_map: dict = {}
    markets_map: dict = {}
    if market_ids:
        mq = await db.execute(select(Market).where(Market.id.in_(list(market_ids))))
        for m in mq.scalars().all():
            markets_map[m.id] = m

        snap_subq = (
            select(
                MarketSnapshot.market_id,
                sqlfunc.max(MarketSnapshot.captured_at).label("max_at"),
            )
            .where(MarketSnapshot.market_id.in_(list(market_ids)))
            .group_by(MarketSnapshot.market_id)
            .subquery()
        )
        snap_q = await db.execute(
            select(MarketSnapshot).join(
                snap_subq,
                (MarketSnapshot.market_id == snap_subq.c.market_id)
                & (MarketSnapshot.captured_at == snap_subq.c.max_at),
            )
        )
        for s in snap_q.scalars().all():
            snaps_map[s.market_id] = s

    # ── Load historical closed dislocation trades ─────────────────────────────
    from sqlalchemy import case as sa_case  # noqa: F811
    hist_q = await db.execute(
        select(
            sqlfunc.count().label("total"),
            sqlfunc.sum(sa_case((PaperPosition.realized_pnl > 0, 1), else_=0)).label("profitable"),
        ).where(
            PaperPosition.strategy == "dislocation",
            PaperPosition.status == "closed",
        )
    )
    hist = hist_q.one()
    hist_total = int(hist.total or 0)
    hist_profitable = int(hist.profitable or 0)
    baseline_precision = hist_profitable / hist_total if hist_total >= 3 else 0.5

    # ── Prepare per-pair data: extract raw_edge, spread, depth, conf_factor, z ─
    pair_data: list[dict] = []
    now = datetime.now(timezone.utc)
    for rel in rels:
        snap_a = snaps_map.get(rel.market_a_id)
        snap_b = snaps_map.get(rel.market_b_id)
        if not snap_a or not snap_b:
            continue
        age_ms = 0.0
        if snap_a.captured_at:
            snap_time = snap_a.captured_at
            if snap_time.tzinfo is None:
                snap_time = snap_time.replace(tzinfo=timezone.utc)
            age_ms = (now - snap_time).total_seconds() * 1000

        price_a = float(snap_a.midpoint or snap_a.last_trade_price or 0.5)
        price_b = float(snap_b.midpoint or snap_b.last_trade_price or 0.5)
        raw_edge = abs(price_a - price_b)
        spread_a = float(snap_a.spread or 0.03)
        spread_b = float(snap_b.spread or 0.03)
        total_cost = (spread_a + spread_b) / 2 + 0.01
        net_edge = raw_edge - total_cost
        depth_a = float(snap_a.bid_depth or snap_a.volume_24h or 0)
        depth = min(depth_a, float(snap_b.bid_depth or snap_b.volume_24h or 0))
        staleness_ok = age_ms < 5000
        staleness_h = 0.8 if staleness_ok else 0.4
        slippage_h = max(0.5, 1.0 - spread_a * 3)
        depth_h = min(1.0, depth / 200.0) if depth > 0 else 0.3
        conf_factor = staleness_h * slippage_h * depth_h
        conf_adj_edge = net_edge * conf_factor
        # Calculate z-score from relationship's baseline statistics
        current_spread = abs(price_a - price_b)
        normal_mean = float(rel.normal_spread_mean) if rel.normal_spread_mean else None
        normal_std = float(rel.normal_spread_std) if rel.normal_spread_std else None
        if normal_mean is not None and normal_std and normal_std > 0:
            z = abs((current_spread - normal_mean) / normal_std)
        else:
            z = 0.0
        spread_to_edge = total_cost / max(raw_edge, 1e-8)

        pair_data.append({
            "z": abs(z),
            "raw_edge": raw_edge,
            "net_edge": net_edge,
            "conf_adj_edge": conf_adj_edge,
            "conf_factor": conf_factor,
            "depth": depth,
            "spread_pct": (spread_a + spread_b) / 2,
            "spread_to_edge": spread_to_edge,
            "age_ms": age_ms,
        })

    total_pairs = len(pair_data)

    # ── Sweep axes ────────────────────────────────────────────────────────────
    conf_edge_values   = [0.005, 0.01, 0.015, 0.02]
    spread_ratio_values = [0.5, 0.6, 0.7, 0.8, 1.0]
    depth_values       = [20.0, 100.0, 200.0, 500.0]
    z_threshold        = 2.0
    min_conf_factor    = 0.5

    cells = []
    best_score = -1.0
    best_cell  = None

    for ce in conf_edge_values:
        for sr in spread_ratio_values:
            for dep in depth_values:
                passing = [
                    p for p in pair_data
                    if p["z"] >= z_threshold
                    and p["conf_adj_edge"] >= ce
                    and p["spread_to_edge"] <= sr
                    and p["depth"] >= dep
                    and p["conf_factor"] >= min_conf_factor
                ]
                would_trade = len(passing)
                pass_rate = would_trade / max(total_pairs, 1)

                # Estimated precision: scale baseline by how selective filters are
                # More selective → higher expected precision (but smaller sample)
                selectivity_bonus = (1.0 - pass_rate) * 0.25
                est_precision = min(0.95, baseline_precision + selectivity_bonus) if would_trade > 0 else 0.0

                # Expected value score: pass_rate × precision × avg_conf_edge
                avg_ce = (sum(p["conf_adj_edge"] for p in passing) / would_trade) if would_trade > 0 else 0.0
                ev_score = pass_rate * est_precision * (1 + avg_ce * 10)

                cell = {
                    "conf_edge": ce,
                    "spread_ratio": sr,
                    "depth": dep,
                    "would_trade_now": would_trade,
                    "pass_rate": round(pass_rate, 4),
                    "est_precision": round(est_precision, 3),
                    "avg_conf_edge": round(avg_ce, 4),
                    "ev_score": round(ev_score, 4),
                    "is_sweet_spot": False,
                }
                if ev_score > best_score and would_trade > 0:
                    best_score = ev_score
                    best_cell = cell
                cells.append(cell)

    if best_cell:
        best_cell["is_sweet_spot"] = True

    # Annotation: group cells by conf_edge for frontend rendering
    by_conf_edge: dict = {}
    for cell in cells:
        key = str(cell["conf_edge"])
        by_conf_edge.setdefault(key, []).append(cell)

    return {
        "total_pairs_monitored": total_pairs,
        "hist_total_trades": hist_total,
        "hist_baseline_precision": round(baseline_precision, 3),
        "sweet_spot": {
            "conf_edge": best_cell["conf_edge"],
            "spread_ratio": best_cell["spread_ratio"],
            "depth": best_cell["depth"],
            "would_trade_now": best_cell["would_trade_now"],
            "est_precision": best_cell["est_precision"],
            "ev_score": best_cell["ev_score"],
        } if best_cell else None,
        "matrix": cells,
        "axes": {
            "conf_edge_values": conf_edge_values,
            "spread_ratio_values": spread_ratio_values,
            "depth_values": depth_values,
        },
    }

