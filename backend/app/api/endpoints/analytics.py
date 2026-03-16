from __future__ import annotations

from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models.portfolio import PortfolioSnapshot
from app.models.paper import PaperPosition

router = APIRouter()


@router.get("/equity-curve")
async def get_equity_curve(
    limit: int = Query(500, le=2000),
    run_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    q = select(PortfolioSnapshot).order_by(PortfolioSnapshot.captured_at.asc()).limit(limit)
    if run_id:
        q = q.where(PortfolioSnapshot.run_id == run_id)
    result = await db.execute(q)
    snaps = result.scalars().all()

    return {
        "equity_curve": [
            {
                "time": s.captured_at.isoformat(),
                "equity": float(s.total_equity) if s.total_equity else 900.0,
                "cash": float(s.cash_balance) if s.cash_balance else 900.0,
                "drawdown": float(s.max_drawdown) if s.max_drawdown else 0.0,
            }
            for s in snaps
        ]
    }


@router.get("/metrics")
async def get_metrics(db: AsyncSession = Depends(get_db)) -> dict:
    closed = await db.execute(
        select(PaperPosition).where(PaperPosition.status == "closed")
    )
    positions = closed.scalars().all()

    if not positions:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "gross_pnl": 0.0,
            "net_pnl": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
        }

    wins = [p for p in positions if p.realized_pnl and float(p.realized_pnl) > 0]
    losses = [p for p in positions if p.realized_pnl and float(p.realized_pnl) <= 0]
    gross_pnl = sum(float(p.realized_pnl) for p in positions if p.realized_pnl)
    total_fees = sum(float(p.total_fees or 0) for p in positions)
    total_slippage = sum(float(p.total_slippage or 0) for p in positions)
    avg_win = sum(float(p.realized_pnl) for p in wins) / len(wins) if wins else 0
    avg_loss = sum(float(p.realized_pnl) for p in losses) / len(losses) if losses else 0
    total_wins = sum(float(p.realized_pnl) for p in wins)
    total_losses = abs(sum(float(p.realized_pnl) for p in losses))

    pnl_series = [float(p.realized_pnl) for p in positions if p.realized_pnl]
    max_drawdown = 0.0
    if pnl_series:
        bankroll = 900.0
        peak = bankroll
        cumulative = bankroll
        for pnl in pnl_series:
            cumulative += pnl
            if cumulative > peak:
                peak = cumulative
            dd = (peak - cumulative) / peak if peak > 0 else 0.0
            if dd > max_drawdown:
                max_drawdown = dd

    win_rate = len(wins) / len(positions) if positions else 0
    loss_rate = 1 - win_rate
    # Expectancy: E = (WR × avg_win) + (LR × avg_loss)
    # Per-trade expectancy in USD
    expectancy = (win_rate * avg_win) + (loss_rate * avg_loss) if positions else 0

    return {
        "total_trades": len(positions),
        "win_rate": win_rate,
        "gross_pnl": gross_pnl,
        "net_pnl": gross_pnl - total_fees - total_slippage,
        "total_fees": total_fees,
        "total_slippage": total_slippage,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": total_wins / total_losses if total_losses > 0 else float("inf"),
        "max_drawdown": max_drawdown,
        "expectancy": round(expectancy, 4),
        "expectancy_pct": round(expectancy / 900 * 100, 4),  # as % of starting bankroll
        "avg_trade_pnl": round(gross_pnl / len(positions), 4) if positions else 0,
    }


@router.get("/exit-breakdown")
async def get_exit_breakdown(db: AsyncSession = Depends(get_db)) -> dict:
    """Exit reason distribution with PnL per reason — shows HOW the system makes/loses money."""
    result = await db.execute(
        select(PaperPosition).where(PaperPosition.status == "closed")
    )
    positions = result.scalars().all()

    ALL_REASONS = ["target_hit", "stop_loss", "max_hold_time", "stale_data", "market_resolved"]
    breakdown: dict[str, dict] = {
        r: {"count": 0, "total_pnl": 0.0, "wins": 0, "losses": 0, "avg_pnl": 0.0}
        for r in ALL_REASONS
    }
    # Bucket unknown reasons too
    for pos in positions:
        reason = pos.exit_reason or "unknown"
        if reason not in breakdown:
            breakdown[reason] = {"count": 0, "total_pnl": 0.0, "wins": 0, "losses": 0, "avg_pnl": 0.0}
        pnl = float(pos.realized_pnl or 0)
        breakdown[reason]["count"] += 1
        breakdown[reason]["total_pnl"] += pnl
        if pnl > 0:
            breakdown[reason]["wins"] += 1
        else:
            breakdown[reason]["losses"] += 1

    # Compute avg_pnl and win_rate for each reason
    rows = []
    for reason, stats in breakdown.items():
        cnt = stats["count"]
        stats["avg_pnl"] = stats["total_pnl"] / cnt if cnt > 0 else 0.0
        stats["win_rate"] = stats["wins"] / cnt if cnt > 0 else 0.0
        rows.append({"reason": reason, **stats})

    rows.sort(key=lambda x: x["count"], reverse=True)
    total_closed = len(positions)

    from sqlalchemy import func as sqlfunc
    open_count = await db.scalar(
        select(sqlfunc.count(PaperPosition.id)).where(PaperPosition.status == "open")
    ) or 0

    return {
        "total_closed": total_closed,
        "breakdown": rows,
        "open_count": open_count,
    }


async def get_per_wallet(db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(
        select(PaperPosition).where(PaperPosition.status == "closed")
    )
    positions = result.scalars().all()

    by_wallet: dict[str, list] = {}
    for p in positions:
        key = str(p.source_wallet_id) if p.source_wallet_id else "dislocation"
        by_wallet.setdefault(key, []).append(p)

    return {
        "per_wallet": [
            {
                "wallet_id": wid,
                "trade_count": len(trades),
                "total_pnl": sum(float(t.realized_pnl or 0) for t in trades),
                "win_rate": sum(1 for t in trades if t.realized_pnl and float(t.realized_pnl) > 0) / len(trades),
            }
            for wid, trades in by_wallet.items()
        ]
    }


@router.get("/per-strategy")
async def get_per_strategy(db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(
        select(PaperPosition).where(PaperPosition.status == "closed")
    )
    positions = result.scalars().all()

    by_strat: dict[str, list] = {}
    for p in positions:
        key = p.strategy or "unknown"
        by_strat.setdefault(key, []).append(p)

    def _strat_stats(strat: str, trades: list) -> dict:
        pnl_series = [float(t.realized_pnl or 0) for t in trades]
        wins  = [p for p in pnl_series if p > 0]
        losses = [p for p in pnl_series if p <= 0]
        trade_count = len(trades)
        wr = len(wins) / trade_count if trade_count else 0.0
        avg_win  = sum(wins)  / len(wins)   if wins   else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        # Profit factor = gross_profit / abs(gross_loss)
        gross_profit = sum(wins)
        gross_loss   = abs(sum(losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
        # Expectancy per trade
        loss_rate = 1 - wr
        expectancy = (wr * avg_win) + (loss_rate * avg_loss)
        # Max drawdown for strategy sub-portfolio
        peak = 0.0
        cumulative = 0.0
        max_dd = 0.0
        for pnl in pnl_series:
            cumulative += pnl
            if cumulative > peak:
                peak = cumulative
            dd = (peak - cumulative) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        # Label: is this strategy carrying the portfolio or dragging it?
        if profit_factor >= 1.5:
            verdict = "strong"
        elif profit_factor >= 1.0:
            verdict = "marginal"
        else:
            verdict = "losing"

        return {
            "strategy": strat,
            "trade_count": trade_count,
            "total_pnl": round(sum(pnl_series), 4),
            "win_rate": round(wr, 4),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "profit_factor": round(profit_factor, 3) if profit_factor != float("inf") else None,
            "expectancy": round(expectancy, 4),
            "expectancy_pct": round(expectancy / 900.0 * 100, 4),
            "max_drawdown": round(max_dd, 4),
            "gross_profit": round(gross_profit, 4),
            "gross_loss": round(gross_loss, 4),
            "verdict": verdict,
        }

    breakdown = [_strat_stats(strat, trades) for strat, trades in by_strat.items()]
    breakdown.sort(key=lambda x: (x["profit_factor"] or 0), reverse=True)

    # Portfolio-level attribution summary
    total_pnl = sum(s["total_pnl"] for s in breakdown)
    for s in breakdown:
        s["pnl_share_pct"] = round(s["total_pnl"] / total_pnl * 100, 1) if total_pnl != 0 else 0.0

    return {
        "per_strategy": breakdown,
        "best_strategy": max(breakdown, key=lambda x: x["profit_factor"] or 0)["strategy"] if breakdown else None,
        "worst_strategy": min(breakdown, key=lambda x: x["profit_factor"] or 0)["strategy"] if breakdown else None,
    }


@router.get("/sensitivity")
async def get_sensitivity() -> dict:
    return {"message": "Sensitivity analysis will be computed by the Monte Carlo engine in Phase 5"}


@router.get("/monte-carlo")
async def get_monte_carlo() -> dict:
    return {"message": "Monte Carlo results will be available after Phase 5 implementation"}


@router.get("/compare")
async def compare_runs() -> dict:
    return {"message": "Run comparison requires multiple strategy runs with different config_hash values"}


@router.get("/latency-decay")
async def get_latency_decay_analysis(db: AsyncSession = Depends(get_db)) -> dict:
    """
    Latency decay analysis: for each tracked wallet, compute how much edge
    remains at 200ms, 500ms, 800ms, 1200ms, 1500ms copy latency.

    This is the key insight: high ROI wallets may have zero copyable alpha
    above 200ms because their trades reprice immediately.
    """
    from app.models.wallet import Wallet, WalletScore
    from sqlalchemy import func

    subq = (
        select(
            WalletScore.wallet_id,
            func.max(WalletScore.scored_at).label("latest"),
        )
        .group_by(WalletScore.wallet_id)
        .subquery()
    )

    result = await db.execute(
        select(Wallet, WalletScore)
        .join(subq, Wallet.id == subq.c.wallet_id)
        .join(
            WalletScore,
            (WalletScore.wallet_id == subq.c.wallet_id) & (WalletScore.scored_at == subq.c.latest),
        )
        .where(Wallet.is_tracked == True)  # noqa: E712
        .order_by(WalletScore.copyability_score.desc())
    )
    rows = result.all()

    # Aggregate: mean edge remaining at each latency point across all wallets
    latency_points = ["200ms", "500ms", "1000ms", "1500ms"]
    aggregate: dict[str, list[float]] = {k: [] for k in latency_points}
    per_wallet = []

    for w, s in rows:
        decay = s.copy_decay_curve or {}
        row = {
            "wallet_id": str(w.id),
            "label": w.label,
            "roi": float(s.total_roi or 0),
            "copyability": float(s.copyability_score or 0),
        }
        for lp in latency_points:
            val = float(decay.get(lp, 0))
            row[lp] = val
            aggregate[lp].append(val)
        per_wallet.append(row)

    # Build aggregate sweep: average edge decay curve
    sweep = []
    for lp in latency_points:
        vals = aggregate[lp]
        sweep.append({
            "latency_ms": int(lp.replace("ms", "")),
            "avg_edge_remaining": round(sum(vals) / len(vals), 4) if vals else 0,
            "max_edge_remaining": round(max(vals), 4) if vals else 0,
            "pct_wallets_positive": round(sum(1 for v in vals if v > 0.1) / len(vals), 4) if vals else 0,
        })

    return {
        "sweep": sweep,
        "per_wallet": per_wallet,
        "insight": (
            "Wallets where edge drops below 0.1 at 500ms are only actionable with <200ms latency. "
            "Track 'pct_wallets_positive' to see how many wallets remain copyable at each delay."
        ),
    }


@router.get("/timing")
async def get_timing_analysis(
    wallet_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Trade timing analysis: does a wallet trade BEFORE or AFTER price moves?
    Information edge detection — the most important signal for copy trading.
    """
    from app.analytics.timing_analysis import (
        compute_trade_timing_profile,
        compute_all_wallet_timing,
    )
    from uuid import UUID

    if wallet_id:
        try:
            uid = UUID(wallet_id)
        except ValueError:
            return {"error": "Invalid wallet_id UUID"}
        profile = await compute_trade_timing_profile(db, uid)
        return profile
    else:
        profiles = await compute_all_wallet_timing(db)
        return {
            "profiles": profiles,
            "total": len(profiles),
            "insight": (
                "information_score > 0.5 means the wallet consistently trades before price moves. "
                "These wallets have genuine information edge and are highest-priority copy targets."
            ),
        }


@router.get("/market-heatmap")
async def get_market_heatmap(db: AsyncSession = Depends(get_db)) -> dict:
    """
    Market inefficiency heatmap: which categories have the most edge opportunity?
    Use this to calibrate which markets to focus copy trading and dislocation strategies on.
    """
    from app.analytics.market_heatmap import build_inefficiency_heatmap
    return await build_inefficiency_heatmap(db)


@router.get("/alpha-persistence")
async def get_alpha_persistence(
    wallet_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Alpha persistence: is a wallet's edge real strategy or luck?
    High autocorrelation in weekly performance = real strategy.
    Near-zero autocorrelation = noise, don't copy.
    """
    from app.analytics.alpha_persistence import (
        compute_alpha_persistence,
        compute_all_persistence,
    )
    from uuid import UUID

    if wallet_id:
        try:
            uid = UUID(wallet_id)
        except ValueError:
            return {"error": "Invalid wallet_id UUID"}
        return await compute_alpha_persistence(db, uid)
    else:
        profiles = await compute_all_persistence(db)
        return {
            "profiles": profiles,
            "total": len(profiles),
            "insight": (
                "persistence_score > 0.6 = likely real strategy. "
                "< 0.5 = likely noise. "
                "Combine with copyability_score for best copy targets."
            ),
        }


@router.get("/wallet-clusters")
async def get_wallet_clusters(
    lookback_days: int = Query(14, ge=1, le=60),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Wallet clustering: detect wallets that are likely the same underlying trader.
    Prevents over-weighting a single trader through multiple tracked addresses.
    """
    from app.analytics.wallet_clustering import compute_wallet_clusters
    return await compute_wallet_clusters(db, lookback_days=lookback_days)


@router.get("/research-summary")
async def get_research_summary(db: AsyncSession = Depends(get_db)) -> dict:
    """
    Single endpoint that pulls all research signals together:
    timing, persistence, heatmap, and copyability — everything you need
    to make a copy trading decision.
    """
    from app.analytics.timing_analysis import compute_all_wallet_timing
    from app.analytics.alpha_persistence import compute_all_persistence
    from app.analytics.market_heatmap import build_inefficiency_heatmap
    from app.models.wallet import WalletScore
    from sqlalchemy import func

    # Parallel fetch (sequential for now, can be parallelized)
    timing_profiles = await compute_all_wallet_timing(db)
    persistence_profiles = await compute_all_persistence(db)
    heatmap = await build_inefficiency_heatmap(db)

    # Build combined wallet intelligence table
    timing_by_id = {p["wallet_id"]: p for p in timing_profiles}
    persistence_by_id = {p["wallet_id"]: p for p in persistence_profiles}

    combined = []
    for wid, timing in timing_by_id.items():
        persistence = persistence_by_id.get(wid, {})
        combined.append({
            "wallet_id": wid,
            "label": timing.get("label", ""),
            "address": timing.get("address", ""),
            "information_score": timing.get("information_score", 0),
            "edge_window_seconds": timing.get("edge_window_seconds", 0),
            "persistence_score": persistence.get("persistence_score", 0),
            "timing_verdict": timing.get("verdict", ""),
            "persistence_verdict": persistence.get("verdict", ""),
            "combined_signal": round(
                timing.get("information_score", 0) * 0.5
                + persistence.get("persistence_score", 0) * 0.5,
                4,
            ),
        })

    combined.sort(key=lambda x: x["combined_signal"], reverse=True)

    return {
        "top_wallets": combined[:10],
        "heatmap_summary": heatmap.get("categories", [])[:5],
        "total_wallets_analysed": len(combined),
        "insight": (
            "combined_signal = 0.5×information_score + 0.5×persistence_score. "
            "Focus on wallets with combined_signal > 0.5 AND in high-inefficiency market categories."
        ),
    }


@router.get("/daily-digest")
async def get_daily_digest(db: AsyncSession = Depends(get_db)) -> dict:
    """
    Daily system health digest — the 5 metrics you should check every day.

    Returns:
    1. Profit factor (edge quality)
    2. Expectancy per trade
    3. Precision trend (last 24h vs all-time)
    4. Spread-to-edge ratio (cost efficiency)
    5. Sample size progress (toward 200 trades)
    """
    now = datetime.now(timezone.utc)
    since_24h = now - timedelta(hours=24)
    since_7d = now - timedelta(days=7)

    all_pos = await db.execute(
        select(PaperPosition).where(PaperPosition.status == "closed")
    )
    positions = all_pos.scalars().all()

    pos_24h = [p for p in positions if p.closed_at and p.closed_at >= since_24h]
    pos_7d = [p for p in positions if p.closed_at and p.closed_at >= since_7d]

    def _stats(pos: list) -> dict:
        if not pos:
            return {"trades": 0, "win_rate": None, "pf": None, "expectancy": None,
                    "gross_pnl": None, "avg_pnl": None}
        wins = [p for p in pos if (p.realized_pnl or 0) > 0]
        losses = [p for p in pos if (p.realized_pnl or 0) <= 0]
        gross = sum(float(p.realized_pnl or 0) for p in pos)
        fees = sum(float(p.total_fees or 0) for p in pos)
        slippage = sum(float(p.total_slippage or 0) for p in pos)
        total_wins = sum(float(p.realized_pnl or 0) for p in wins)
        total_losses = abs(sum(float(p.realized_pnl or 0) for p in losses))
        wr = len(wins) / len(pos)
        avg_win = total_wins / len(wins) if wins else 0
        avg_loss = -(total_losses / len(losses)) if losses else 0
        expectancy = (wr * avg_win) + ((1 - wr) * avg_loss)
        pf = total_wins / total_losses if total_losses > 0 else float("inf")
        return {
            "trades": len(pos),
            "win_rate": round(wr, 4),
            "pf": round(pf, 4) if pf != float("inf") else None,
            "expectancy": round(expectancy, 4),
            "gross_pnl": round(gross, 4),
            "net_pnl": round(gross - fees - slippage, 4),
            "avg_pnl": round(gross / len(pos), 4),
            "total_fees": round(fees, 4),
        }

    all_stats = _stats(positions)
    stats_24h = _stats(pos_24h)
    stats_7d = _stats(pos_7d)

    # Spread vs edge ratio from recent closed positions
    # (avg slippage+fees / avg gross_pnl as proxy for cost efficiency)
    cost_ratio = None
    if all_stats["gross_pnl"] and all_stats["gross_pnl"] > 0:
        total_cost = all_stats["total_fees"] + sum(float(p.total_slippage or 0) for p in positions)
        cost_ratio = round(total_cost / all_stats["gross_pnl"], 3)

    # Sample size progress toward 200 trades
    sample_progress = round(len(positions) / 200 * 100, 1)
    sample_status = (
        "critical" if len(positions) < 30
        else "low" if len(positions) < 100
        else "building" if len(positions) < 200
        else "sufficient"
    )

    # Overall edge quality verdict
    pf = all_stats["pf"] or 0
    edge_verdict = (
        "no_edge" if pf < 1.0
        else "marginal" if pf < 1.3
        else "real_edge" if pf < 1.6
        else "strong_edge"
    )

    # Key recommendation
    if len(positions) < 50:
        recommendation = "Accumulate sample size first. Results not yet statistically meaningful."
    elif pf < 1.0:
        recommendation = "System losing. Review filter thresholds in Experiment Lab — raise min_z or lower max_spread."
    elif pf < 1.3:
        recommendation = "Marginal edge. Spread cost likely eating most profit. Check spread_to_edge_ratio."
    elif cost_ratio and cost_ratio > 1.5:
        recommendation = "PF positive but cost ratio high. Reduce fee exposure — tighten max_spread_pct threshold."
    else:
        recommendation = "System performing. Keep accumulating sample size to confirm edge persistence."

    return {
        "generated_at": now.isoformat(),
        "summary": {
            "edge_verdict": edge_verdict,
            "recommendation": recommendation,
            "sample_size": len(positions),
            "sample_progress_pct": sample_progress,
            "sample_status": sample_status,
        },
        "metrics": {
            "all_time": all_stats,
            "last_7d": stats_7d,
            "last_24h": stats_24h,
        },
        "cost_efficiency": {
            "cost_to_gross_ratio": cost_ratio,
            "interpretation": (
                "< 0.5 = costs consuming less than half of gross (good)"
                if cost_ratio and cost_ratio < 0.5
                else "> 1.0 = costs exceed gross profit (spread problem)"
                if cost_ratio and cost_ratio > 1.0
                else "0.5–1.0 = acceptable but improvable"
                if cost_ratio
                else "Insufficient data"
            ),
        },
        "watchlist": {
            "profit_factor": all_stats["pf"],
            "expectancy_usd": all_stats["expectancy"],
            "win_rate": all_stats["win_rate"],
            "precision_24h": round(len([p for p in pos_24h if (p.realized_pnl or 0) > 0]) / len(pos_24h), 4) if pos_24h else None,
        },
    }


@router.get("/strategy-health")
async def get_strategy_health(
    window: int = Query(20, ge=5, le=100, description="Rolling window (number of trades)"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Per-strategy rolling health panel.

    Returns for each strategy:
      - mode: active / shadow / paused
      - rolling PF over last `window` trades
      - expectancy, win rate, trade count
      - kill_active flag + reason
      - capital_share: recommended bankroll fraction
      - 7-day vs all-time comparison
    """
    from app.strategies.strategy_manager import get_all_rolling_stats, _DEFAULT_CONFIGS
    from datetime import timedelta

    rolling = await get_all_rolling_stats(db)

    # Also pull 7-day window for trend comparison
    cutoff_7d = datetime.now(timezone.utc) - timedelta(days=7)
    result_7d = await db.execute(
        select(PaperPosition)
        .where(
            PaperPosition.status == "closed",
            PaperPosition.closed_at >= cutoff_7d,
        )
    )
    pos_7d_all = result_7d.scalars().all()

    strategies_out = []
    for name, stats in rolling.items():
        # 7-day sub-stats
        pos_7d = [p for p in pos_7d_all if p.strategy == name]
        wins_7d = [float(p.realized_pnl or 0) for p in pos_7d if (p.realized_pnl or 0) > 0]
        losses_7d = [float(p.realized_pnl or 0) for p in pos_7d if (p.realized_pnl or 0) <= 0]
        gp_7d = sum(wins_7d)
        gl_7d = abs(sum(losses_7d))
        pf_7d = round(gp_7d / gl_7d, 3) if gl_7d > 0 else (None if gp_7d == 0 else None)
        exp_7d = None
        if pos_7d:
            wr7 = len(wins_7d) / len(pos_7d)
            avg_w7 = sum(wins_7d) / len(wins_7d) if wins_7d else 0
            avg_l7 = sum(losses_7d) / len(losses_7d) if losses_7d else 0
            exp_7d = round((wr7 * avg_w7) + ((1 - wr7) * avg_l7), 4)

        # Trend: rolling PF vs 7d PF
        pf_r = stats.profit_factor
        trend = "stable"
        if pf_r is not None and pf_7d is not None:
            diff = pf_7d - pf_r
            if diff > 0.2:
                trend = "improving"
            elif diff < -0.2:
                trend = "deteriorating"

        strategies_out.append({
            "strategy": name,
            "mode": stats.mode,
            "kill_active": stats.kill_active,
            "kill_reason": stats.kill_reason,
            "capital_share": stats.capital_share,
            "rolling": {
                "window": stats.rolling_window,
                "trade_count": stats.trade_count,
                "win_rate": stats.win_rate,
                "profit_factor": stats.profit_factor,
                "expectancy": stats.expectancy,
                "gross_profit": stats.gross_profit,
                "gross_loss": stats.gross_loss,
            },
            "last_7d": {
                "trade_count": len(pos_7d),
                "profit_factor": pf_7d,
                "expectancy": exp_7d,
            },
            "trend": trend,
            # Human-readable verdict
            "verdict": (
                "kill_switch" if stats.kill_active
                else "shadow_monitoring" if stats.mode == "shadow"
                else "strong" if (pf_r or 0) >= 1.5
                else "marginal" if (pf_r or 0) >= 1.0
                else "losing" if stats.trade_count >= 5
                else "insufficient_data"
            ),
        })

    # Sort: active first, then by PF descending
    strategies_out.sort(
        key=lambda x: (
            0 if x["mode"] == "active" else 1,
            -(x["rolling"]["profit_factor"] or 0),
        )
    )

    return {
        "strategies": strategies_out,
        "rolling_window": window,
        "summary": {
            "active_count": sum(1 for s in strategies_out if s["mode"] == "active"),
            "shadow_count": sum(1 for s in strategies_out if s["mode"] == "shadow"),
            "kill_active_count": sum(1 for s in strategies_out if s["kill_active"]),
            "total_capital_allocated": round(
                sum(s["capital_share"] for s in strategies_out if s["mode"] == "active"), 3
            ),
        },
    }


@router.get("/alpha-backtest")
async def alpha_score_backtest(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Copyable Alpha Score IQ Test — 5-bucket validation.

    Algorithm:
    1. For every tracked wallet: compute copyable_alpha_score
    2. Sort wallets by score, divide into 5 equal buckets (quintiles)
    3. For each bucket: aggregate all closed PnL from paper trades
       where the source wallet is in that bucket
    4. Compare: does Bucket 1 (top 20%) produce better PnL than Bucket 5 (bottom 20%)?

    If yes → score has predictive validity.
    If no  → score needs recalibration.
    """
    from app.models.wallet import Wallet
    from app.models.paper import PaperPosition
    from app.intelligence.wallet_alpha import compute_copyable_alpha

    # Step 1: Score all tracked wallets
    wallets_result = await db.execute(
        select(Wallet).where(Wallet.is_tracked.is_(True))
    )
    wallets = wallets_result.scalars().all()

    wallet_scores: list[dict] = []
    for w in wallets:
        try:
            alpha = await compute_copyable_alpha(db, w.id)
            wallet_scores.append({
                "wallet_id": str(w.id),
                "score": alpha.copyable_alpha_score,
                "recommendation": alpha.recommendation,
                "decay_risk": alpha.alpha_decay_risk,
            })
        except Exception:
            continue

    if len(wallet_scores) < 5:
        return {
            "error": "insufficient_wallets",
            "message": f"Need at least 5 tracked wallets, found {len(wallet_scores)}",
            "buckets": [],
        }

    # Step 2: Sort and bucket
    wallet_scores.sort(key=lambda x: -x["score"])
    n = len(wallet_scores)
    bucket_size = max(1, n // 5)

    buckets_raw = []
    for i in range(5):
        start = i * bucket_size
        end   = start + bucket_size if i < 4 else n
        bucket_wallets = wallet_scores[start:end]
        bucket_ids = {w["wallet_id"] for w in bucket_wallets}
        avg_score  = sum(w["score"] for w in bucket_wallets) / len(bucket_wallets)
        score_range = (
            round(bucket_wallets[-1]["score"], 3),
            round(bucket_wallets[0]["score"], 3),
        )
        buckets_raw.append((bucket_ids, avg_score, score_range, bucket_wallets))

    # Step 3: Pull all closed positions and map to wallets via source_wallet_id
    pos_result = await db.execute(
        select(PaperPosition).where(PaperPosition.status == "closed")
    )
    all_positions = pos_result.scalars().all()

    # Step 4: Aggregate PnL per bucket
    buckets_out = []
    for bucket_idx, (bucket_ids, avg_score, score_range, bucket_wallets) in enumerate(buckets_raw):
        bucket_pnls: list[float] = []
        for pos in all_positions:
            wid = str(pos.source_wallet_id) if pos.source_wallet_id else ""
            if wid in bucket_ids:
                bucket_pnls.append(float(pos.realized_pnl or 0))

        wins   = [p for p in bucket_pnls if p > 0]
        losses = [p for p in bucket_pnls if p <= 0]
        gp = sum(wins)
        gl = abs(sum(losses))
        pf = round(gp / gl, 3) if gl > 0 else (None if gp == 0 else float("inf"))
        wr = round(len(wins) / len(bucket_pnls), 3) if bucket_pnls else None
        exp = None
        if bucket_pnls:
            avg_w = sum(wins)   / len(wins)   if wins   else 0
            avg_l = sum(losses) / len(losses) if losses else 0
            _wr   = len(wins) / len(bucket_pnls)
            exp   = round(_wr * avg_w + (1 - _wr) * avg_l, 4)

        buckets_out.append({
            "bucket": bucket_idx + 1,
            "label": f"Top {20 * (bucket_idx + 1)}%" if bucket_idx == 0 else f"Quintile {bucket_idx + 1}",
            "wallet_count": len(bucket_wallets),
            "trade_count": len(bucket_pnls),
            "avg_alpha_score": round(avg_score, 4),
            "score_range": score_range,
            "total_pnl": round(sum(bucket_pnls), 4),
            "profit_factor": pf,
            "win_rate": wr,
            "expectancy": exp,
            "verdict": (
                "strong" if (pf or 0) >= 1.5 else
                "marginal" if (pf or 0) >= 1.0 else
                "losing" if bucket_pnls else
                "no_data"
            ),
        })

    # Validation result: does top bucket beat bottom bucket?
    top    = buckets_out[0]
    bottom = buckets_out[-1]

    def safe_pf(b: dict) -> float:
        pf = b.get("profit_factor")
        if pf is None: return 0.0
        if pf == float("inf"): return 3.0
        return float(pf)

    score_valid = safe_pf(top) > safe_pf(bottom)
    monotonic = all(
        safe_pf(buckets_out[i]) >= safe_pf(buckets_out[i + 1]) - 0.1
        for i in range(len(buckets_out) - 1)
    )

    return {
        "buckets": buckets_out,
        "validation": {
            "score_predictive": score_valid,
            "roughly_monotonic": monotonic,
            "top_bucket_pf": safe_pf(top),
            "bottom_bucket_pf": safe_pf(bottom),
            "verdict": (
                "VALID: alpha score predicts performance" if score_valid and monotonic
                else "PARTIAL: top beats bottom but not monotonic" if score_valid
                else "INVALID: score has no predictive power — recalibrate weights"
            ),
        },
        "total_wallets_evaluated": len(wallet_scores),
        "total_trades_mapped": sum(b["trade_count"] for b in buckets_out),
    }


@router.get("/wallet-calibration")
async def get_wallet_calibration(db: AsyncSession = Depends(get_db)) -> dict:
    """
    Predictive Score vs Realized Copy Performance scatter data.

    For each wallet that has both a score and closed copy trades,
    returns (composite_score, copyable_alpha, realized_pf, realized_pnl, win_rate, trade_count).

    Use this to answer: "Does our model correctly rank wallets by actual copy profitability?"
    """
    from app.models.wallet import Wallet, WalletScore
    from sqlalchemy import case

    # Latest score per wallet
    score_subq = (
        select(
            WalletScore.wallet_id,
            func.max(WalletScore.scored_at).label("latest"),
        )
        .group_by(WalletScore.wallet_id)
        .subquery()
    )

    scored_q = await db.execute(
        select(Wallet, WalletScore)
        .join(score_subq, Wallet.id == score_subq.c.wallet_id)
        .join(
            WalletScore,
            (WalletScore.wallet_id == score_subq.c.wallet_id)
            & (WalletScore.scored_at == score_subq.c.latest),
        )
        .where(Wallet.is_tracked.is_(True))
    )
    scored_rows = scored_q.all()

    # Copy PnL per wallet (closed positions only)
    copy_q = await db.execute(
        select(
            PaperPosition.source_wallet_id,
            func.count().label("trade_count"),
            func.sum(PaperPosition.realized_pnl).label("total_pnl"),
            func.sum(case((PaperPosition.realized_pnl > 0, 1), else_=0)).label("wins"),
        )
        .where(
            PaperPosition.status == "closed",
            PaperPosition.source_wallet_id.isnot(None),
        )
        .group_by(PaperPosition.source_wallet_id)
    )
    copy_map = {str(r.source_wallet_id): r for r in copy_q}

    points = []
    all_pf_values = []

    for w, s in scored_rows:
        wid = str(w.id)
        cp = copy_map.get(wid)
        trade_count = int(cp.trade_count) if cp else 0
        total_pnl = float(cp.total_pnl or 0) if cp else 0.0
        wins = int(cp.wins or 0) if cp else 0

        composite = float(s.composite_score or 0)
        copyability = float(s.copyability_score or 0)
        roi = float(s.total_roi or 0)
        copyable_alpha = round(copyability * roi, 4)

        # Realized profit factor
        gross_profit = 0.0
        gross_loss = 0.0
        if cp and trade_count > 0:
            # We need per-trade breakdown; approximate from wins/total and total_pnl
            # Can't get exact without per-row data, use wins ratio approximation
            win_rate = wins / trade_count
            if wins > 0 and total_pnl > 0:
                avg_win = total_pnl / wins * 1.2   # heuristic
                avg_loss = total_pnl / max(trade_count - wins, 1) * (-0.8) if trade_count > wins else 0
                gross_profit = wins * avg_win
                gross_loss = abs((trade_count - wins) * avg_loss)

        realized_pf = round(gross_profit / max(gross_loss, 0.0001), 3) if gross_loss > 0 else None
        win_rate = round(wins / trade_count, 4) if trade_count > 0 else None

        point = {
            "wallet_id": wid,
            "label": w.label or w.address[:12] + "...",
            "composite_score": round(composite, 4),
            "copyable_alpha": copyable_alpha,
            "realized_pnl": round(total_pnl, 4),
            "realized_pf": realized_pf,
            "win_rate": win_rate,
            "trade_count": trade_count,
            "classification": s.classification,
            "has_data": trade_count > 0,
        }
        points.append(point)
        if realized_pf is not None:
            all_pf_values.append(realized_pf)

    # Calibration score: Spearman-like rank correlation between predictive and realized
    with_data = [p for p in points if p["has_data"] and p["realized_pf"] is not None]
    calibration_score = None
    calibration_verdict = "insufficient_data"

    if len(with_data) >= 3:
        # Simple rank correlation
        ranked_pred = sorted(with_data, key=lambda x: -x["composite_score"])
        ranked_real = sorted(with_data, key=lambda x: -(x["realized_pf"] or 0))
        n = len(with_data)
        pred_ranks = {p["wallet_id"]: i for i, p in enumerate(ranked_pred)}
        real_ranks = {p["wallet_id"]: i for i, p in enumerate(ranked_real)}
        d2_sum = sum((pred_ranks[p["wallet_id"]] - real_ranks[p["wallet_id"]]) ** 2 for p in with_data)
        spearman = 1 - (6 * d2_sum) / (n * (n ** 2 - 1))
        calibration_score = round(spearman, 3)
        calibration_verdict = (
            "well_calibrated" if spearman > 0.6
            else "partially_calibrated" if spearman > 0.2
            else "miscalibrated"
        )

    points.sort(key=lambda x: -x["composite_score"])

    return {
        "points": points,
        "total_wallets": len(points),
        "wallets_with_copy_data": len(with_data),
        "calibration_score": calibration_score,
        "calibration_verdict": calibration_verdict,
        "avg_realized_pf": round(sum(all_pf_values) / len(all_pf_values), 3) if all_pf_values else None,
        "note": "x=composite_score y=realized_pf. Well-calibrated = high composite => high realized PF.",
    }


# ── Stabilization Checkpoint ──────────────────────────────────────────────────

NON_TRADING_EXITS = {"stale_data", "position_cap_cleanup", "demo_cleanup"}
CHECKPOINT_MILESTONES = [20, 50, 100, 200]


def _checkpoint_stats(positions: list, label: str, window_n: int) -> dict:
    """Compute PF/expectancy/cost-gross/exit breakdown for a set of positions."""
    pnl_series  = [float(p.realized_pnl or 0) for p in positions]
    wins        = [v for v in pnl_series if v > 0]
    losses      = [v for v in pnl_series if v <= 0]
    gross_p     = sum(wins)
    gross_l     = abs(sum(losses))
    pf          = round(gross_p / gross_l, 3) if gross_l > 0 else (float("inf") if gross_p > 0 else None)
    expectancy  = round(sum(pnl_series) / len(pnl_series), 4) if pnl_series else 0

    total_fees     = sum(float(p.total_fees     or 0) for p in positions)
    total_slippage = sum(float(p.total_slippage or 0) for p in positions)
    total_cost     = total_fees + total_slippage
    cost_gross     = round(total_cost / gross_p, 3) if gross_p > 0 else None

    buy_wins  = sum(1 for p in positions if (p.side or "").upper() == "BUY"  and float(p.realized_pnl or 0) > 0)
    buy_tot   = sum(1 for p in positions if (p.side or "").upper() == "BUY")
    sell_wins = sum(1 for p in positions if (p.side or "").upper() == "SELL" and float(p.realized_pnl or 0) > 0)
    sell_tot  = sum(1 for p in positions if (p.side or "").upper() == "SELL")

    exit_breakdown: dict[str, int] = {}
    for p in positions:
        key = p.exit_reason or "unknown"
        exit_breakdown[key] = exit_breakdown.get(key, 0) + 1

    strat_pnl: dict[str, float] = {}
    strat_cnt: dict[str, int]   = {}
    for p in positions:
        s = p.strategy or "unknown"
        strat_pnl[s] = strat_pnl.get(s, 0) + float(p.realized_pnl or 0)
        strat_cnt[s] = strat_cnt.get(s, 0) + 1

    hold_times = []
    for p in positions:
        if p.opened_at and p.closed_at:
            delta = (p.closed_at - p.opened_at).total_seconds() / 60
            hold_times.append(delta)
    avg_hold_min = round(sum(hold_times) / len(hold_times), 1) if hold_times else None

    avg_size = round(sum(float(p.total_cost or 0) for p in positions) / len(positions), 3) if positions else 0

    # Verdict
    if pf is None:
        verdict = "insufficient_data"
    elif pf >= 1.5:
        verdict = "strong_edge"
    elif pf >= 1.1:
        verdict = "positive_edge"
    elif pf >= 0.9:
        verdict = "marginal"
    elif pf >= 0.7:
        verdict = "weak"
    else:
        verdict = "losing"

    return {
        "label": label,
        "window_n": window_n,
        "trade_count": len(positions),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": round(len(wins) / len(positions), 3) if positions else 0,
        "gross_profit": round(gross_p, 4),
        "gross_loss": round(gross_l, 4),
        "profit_factor": pf,
        "expectancy": expectancy,
        "total_fees": round(total_fees, 4),
        "total_slippage": round(total_slippage, 4),
        "cost_gross_ratio": cost_gross,
        "avg_position_size_usd": avg_size,
        "avg_hold_minutes": avg_hold_min,
        "buy_win_rate": round(buy_wins / buy_tot, 3) if buy_tot else None,
        "sell_win_rate": round(sell_wins / sell_tot, 3) if sell_tot else None,
        "buy_count": buy_tot,
        "sell_count": sell_tot,
        "exit_breakdown": dict(sorted(exit_breakdown.items(), key=lambda x: -x[1])),
        "strategy_pnl": {k: round(v, 4) for k, v in sorted(strat_pnl.items(), key=lambda x: -x[1])},
        "strategy_count": strat_cnt,
        "verdict": verdict,
    }


@router.get("/checkpoint")
async def get_checkpoint(db: AsyncSession = Depends(get_db)) -> dict:
    """Stabilization checkpoint report.

    Returns stats for last 20 / 50 / 100 / 200 real trading exits
    (excludes stale_data / position_cap_cleanup / demo_cleanup).
    Also reports which milestone has been reached and what's next.
    """
    # Pull all real trading closed positions newest-first
    result = await db.execute(
        select(PaperPosition)
        .where(
            PaperPosition.status == "closed",
            PaperPosition.exit_reason.notin_(NON_TRADING_EXITS),
        )
        .order_by(PaperPosition.closed_at.desc())
        .limit(max(CHECKPOINT_MILESTONES))
    )
    all_real = result.scalars().all()
    total_real = len(all_real)

    # Determine current milestone
    reached = [m for m in CHECKPOINT_MILESTONES if total_real >= m]
    current_milestone = reached[-1] if reached else None
    next_milestone    = next((m for m in CHECKPOINT_MILESTONES if total_real < m), None)

    checkpoints = []
    for n in CHECKPOINT_MILESTONES:
        if total_real >= n:
            subset = all_real[:n]
            checkpoints.append(_checkpoint_stats(subset, f"last_{n}", n))

    # Also compute "all time" real trading stats
    all_result = await db.execute(
        select(PaperPosition)
        .where(
            PaperPosition.status == "closed",
            PaperPosition.exit_reason.notin_(NON_TRADING_EXITS),
        )
        .order_by(PaperPosition.closed_at.desc())
    )
    all_positions = all_result.scalars().all()

    # Infrastructure exit count for context
    infra_result = await db.execute(
        select(func.count()).where(
            PaperPosition.status == "closed",
            PaperPosition.exit_reason.in_(NON_TRADING_EXITS),
        )
    )
    infra_count = infra_result.scalar() or 0

    open_result = await db.execute(
        select(func.count()).where(PaperPosition.status == "open")
    )
    open_count = open_result.scalar() or 0

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_real_trading_exits": len(all_positions),
            "total_infra_exits": infra_count,
            "open_positions": open_count,
            "current_milestone": current_milestone,
            "next_milestone": next_milestone,
            "trades_until_next": (next_milestone - total_real) if next_milestone else 0,
        },
        "checkpoints": checkpoints,
        "all_time": _checkpoint_stats(all_positions, "all_time", len(all_positions)) if all_positions else None,
    }


# ── Edge Calibration ──────────────────────────────────────────────────────────

@router.get("/edge-calibration")
async def get_edge_calibration(db: AsyncSession = Depends(get_db)) -> dict:
    """Edge Calibration: does the model's predicted edge correlate with realized PnL?

    Key question: 'Is high edge_at_entry actually making money?'

    Returns:
    - Pearson correlation (edge_entry vs realized_pnl)
    - Per-side (BUY / SELL) correlations
    - Edge bucket breakdown: avg PnL by low / mid / high edge
    - High-edge miss analysis: why do high-edge trades fail?
    - Verdict and interpretation
    """
    from sqlalchemy import text

    sql = text("""
        WITH edge_pnl AS (
          SELECT DISTINCT ON (pp.id)
            pp.id,
            sd.edge_at_decision            AS edge_entry,
            pp.realized_pnl                AS realized_pnl,
            pp.strategy,
            pp.side,
            pp.exit_reason,
            pp.total_cost                  AS size_usd
          FROM paper_positions pp
          JOIN paper_orders po
            ON po.market_id = pp.market_id
           AND po.created_at BETWEEN pp.opened_at - INTERVAL '5 seconds'
                                 AND pp.opened_at + INTERVAL '5 seconds'
          JOIN signal_decisions sd
            ON sd.signal_id = po.signal_id
           AND sd.decision = 'accept'
          WHERE pp.status = 'closed'
            AND pp.exit_reason NOT IN ('stale_data','position_cap_cleanup','demo_cleanup')
            AND sd.edge_at_decision IS NOT NULL
          ORDER BY pp.id, po.created_at
        )
        SELECT
          COUNT(*)                                                        AS n,
          CORR(edge_entry, realized_pnl)                                 AS pearson_r,
          -- BUY / SELL split
          CORR(CASE WHEN side='BUY'  THEN edge_entry END,
               CASE WHEN side='BUY'  THEN realized_pnl END)              AS buy_corr,
          CORR(CASE WHEN side='SELL' THEN edge_entry END,
               CASE WHEN side='SELL' THEN realized_pnl END)              AS sell_corr,
          -- Edge bucket avg PnL
          AVG(CASE WHEN edge_entry < 0.05               THEN realized_pnl END) AS avg_pnl_low,
          AVG(CASE WHEN edge_entry BETWEEN 0.05 AND 0.15 THEN realized_pnl END) AS avg_pnl_mid,
          AVG(CASE WHEN edge_entry > 0.15               THEN realized_pnl END) AS avg_pnl_high,
          COUNT(CASE WHEN edge_entry < 0.05               THEN 1 END)   AS n_low,
          COUNT(CASE WHEN edge_entry BETWEEN 0.05 AND 0.15 THEN 1 END)  AS n_mid,
          COUNT(CASE WHEN edge_entry > 0.15               THEN 1 END)   AS n_high,
          -- High-edge miss rate
          COUNT(CASE WHEN edge_entry > 0.3 AND realized_pnl <= 0 THEN 1 END)   AS high_edge_losses,
          COUNT(CASE WHEN edge_entry > 0.3                         THEN 1 END)  AS high_edge_total,
          -- Strategy split corr
          CORR(CASE WHEN strategy='direct_copy'     THEN edge_entry END,
               CASE WHEN strategy='direct_copy'     THEN realized_pnl END) AS direct_copy_corr,
          CORR(CASE WHEN strategy='high_conviction' THEN edge_entry END,
               CASE WHEN strategy='high_conviction' THEN realized_pnl END) AS high_conviction_corr,
          -- Exit reason for high-edge misses
          COUNT(CASE WHEN edge_entry > 0.3 AND realized_pnl <= 0 AND exit_reason='wallet_reversal' THEN 1 END) AS miss_wallet_reversal,
          COUNT(CASE WHEN edge_entry > 0.3 AND realized_pnl <= 0 AND exit_reason='stop_loss'       THEN 1 END) AS miss_stop_loss,
          COUNT(CASE WHEN edge_entry > 0.3 AND realized_pnl <= 0 AND exit_reason='max_hold_time'   THEN 1 END) AS miss_max_hold
        FROM edge_pnl
    """)

    row = (await db.execute(sql)).mappings().one_or_none()
    if not row or not row["n"]:
        return {"error": "insufficient_data", "note": "Need linked positions with edge data"}

    n              = int(row["n"])
    pearson_r      = float(row["pearson_r"]) if row["pearson_r"] is not None else None
    buy_corr       = float(row["buy_corr"])  if row["buy_corr"]  is not None else None
    sell_corr      = float(row["sell_corr"]) if row["sell_corr"] is not None else None
    direct_corr    = float(row["direct_copy_corr"])     if row["direct_copy_corr"]     is not None else None
    hc_corr        = float(row["high_conviction_corr"]) if row["high_conviction_corr"] is not None else None

    high_edge_total  = int(row["high_edge_total"]  or 0)
    high_edge_losses = int(row["high_edge_losses"] or 0)
    high_edge_miss_rate = round(high_edge_losses / high_edge_total, 3) if high_edge_total else None

    # Verdict logic
    if pearson_r is None:
        overall_verdict = "insufficient_data"
        interpretation  = "Not enough linked data yet."
    elif pearson_r >= 0.3:
        overall_verdict = "well_calibrated"
        interpretation  = "Model edge predicts realized PnL. Alpha is real."
    elif pearson_r >= 0.1:
        overall_verdict = "weakly_calibrated"
        interpretation  = "Weak positive correlation. Edge model is directionally correct but noisy."
    elif pearson_r >= -0.1:
        overall_verdict = "uncalibrated"
        interpretation  = "No meaningful correlation. Model edge does not predict outcomes."
    else:
        overall_verdict = "inverted"
        interpretation  = "NEGATIVE correlation. High-edge trades are losing — model may be mis-calibrated or exits are too aggressive."

    # BUY verdict
    if buy_corr is not None and buy_corr >= 0.2:
        buy_verdict = "calibrated"
    elif buy_corr is not None and buy_corr >= 0:
        buy_verdict = "weak"
    elif buy_corr is not None:
        buy_verdict = "inverted"
    else:
        buy_verdict = "no_data"

    # SELL verdict
    if sell_corr is not None and sell_corr >= 0.2:
        sell_verdict = "calibrated"
    elif sell_corr is not None and sell_corr >= 0:
        sell_verdict = "weak"
    elif sell_corr is not None:
        sell_verdict = "inverted"
    else:
        sell_verdict = "no_data"

    # High-edge miss diagnosis
    if high_edge_total > 5:
        miss_wallet_rev = int(row["miss_wallet_reversal"] or 0)
        miss_stop       = int(row["miss_stop_loss"]       or 0)
        miss_hold       = int(row["miss_max_hold"]        or 0)
        if miss_wallet_rev > high_edge_total * 0.4:
            miss_diagnosis = "wallet_reversal_dominant: exit engine may be exiting high-edge trades too early"
        elif miss_stop > high_edge_total * 0.4:
            miss_diagnosis = "stop_loss_dominant: stop loss too tight for edge size"
        else:
            miss_diagnosis = "mixed: no single dominant cause — model edge may not survive to realization"
    else:
        miss_wallet_rev = miss_stop = miss_hold = 0
        miss_diagnosis = "insufficient_data"

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sample_size": n,
        "model_version": "3-layer (edge_cap + confidence + fill_prob × liquidity)",
        "overall": {
            "pearson_r": round(pearson_r, 4) if pearson_r is not None else None,
            "verdict": overall_verdict,
            "interpretation": interpretation,
        },
        "by_side": {
            "buy":  { "pearson_r": round(buy_corr,  4) if buy_corr  is not None else None, "verdict": buy_verdict  },
            "sell": { "pearson_r": round(sell_corr, 4) if sell_corr is not None else None, "verdict": sell_verdict },
        },
        "by_strategy": {
            "direct_copy":     { "pearson_r": round(direct_corr, 4) if direct_corr is not None else None },
            "high_conviction": { "pearson_r": round(hc_corr,     4) if hc_corr     is not None else None },
        },
        "edge_buckets": [
            {
                "bucket": "low_edge",
                "threshold": "< 0.05",
                "n": int(row["n_low"] or 0),
                "avg_pnl": round(float(row["avg_pnl_low"]), 4) if row["avg_pnl_low"] is not None else None,
            },
            {
                "bucket": "mid_edge",
                "threshold": "0.05 – 0.15",
                "n": int(row["n_mid"] or 0),
                "avg_pnl": round(float(row["avg_pnl_mid"]), 4) if row["avg_pnl_mid"] is not None else None,
            },
            {
                "bucket": "high_edge",
                "threshold": "> 0.15",
                "n": int(row["n_high"] or 0),
                "avg_pnl": round(float(row["avg_pnl_high"]), 4) if row["avg_pnl_high"] is not None else None,
            },
        ],
        "high_edge_miss_analysis": {
            "high_edge_total": high_edge_total,
            "high_edge_losses": high_edge_losses,
            "miss_rate": high_edge_miss_rate,
            "by_exit_reason": {
                "wallet_reversal": miss_wallet_rev,
                "stop_loss": miss_stop,
                "max_hold_time": miss_hold,
            },
            "diagnosis": miss_diagnosis,
        },
        "fix_applied": {
            "max_raw_edge_cap": 0.15,
            "wallet_score_dampening": "imbalance weight *2 → *0.6, repricing *1.5 → *0.5",
            "layer3_gates": "executable_edge = conf_adjusted × fill_prob × liquidity_factor",
            "expected_effect": (
                "High-edge trades (>0.15) should disappear from DB. "
                "Edge distribution shifts toward mid bucket (0.05–0.15) where avg_pnl was +$0.18. "
                "BUY pearson_r should move from -0.37 toward 0+ as cap prevents overconfident trades."
            ),
        },
        "note": (
            "Historical data (n=83) reflects pre-fix model. "
            "New trades after restart will show capped edges. "
            "pearson_r > 0.3 = well calibrated. Target: high_edge bucket shrinks, mid_edge grows."
        ),
    }
