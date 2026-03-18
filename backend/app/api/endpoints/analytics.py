from __future__ import annotations

from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.dependencies import get_db
from app.models.portfolio import PortfolioSnapshot
from app.models.paper import PaperOrder, PaperPosition
from app.models.signal import SignalDecision, TradeSignal
from app.signals.calibration import HybridProbabilityCalibrator

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
                "equity": float(s.total_equity) if s.total_equity else 5000.0,
                "cash": float(s.cash_balance) if s.cash_balance else 5000.0,
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
        from app.config import get_settings as _gs
        bankroll = _gs().STARTING_BALANCE
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
        "expectancy_pct": round(expectancy / 5000 * 100, 4),  # as % of starting bankroll
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


@router.get("/per-wallet")
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
            "expectancy_pct": round(expectancy / 5000.0 * 100, 4),
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


# Trading vs infra exit classification for validation
TRADING_EXITS = {"target_hit", "stop_loss", "wallet_reversal", "market_resolved", "max_hold_time", "ev_compression", "spread_normalized"}
INFRA_EXITS = {"stale_data", "position_cap_cleanup", "demo_cleanup"}


def _resolve_validation_cutoff(cutoff_hours: int) -> tuple[datetime, bool]:
    """Resolve validation cutoff from env or fallback window."""
    import os

    now = datetime.now(timezone.utc)
    cutoff_str = os.getenv("VALIDATION_CUTOFF_AT")
    if cutoff_str:
        try:
            cutoff = datetime.fromisoformat(cutoff_str.replace("Z", "+00:00"))
            return cutoff, True
        except Exception:
            pass
    return now - timedelta(hours=cutoff_hours), False


def _safe_pf(values: list[float]) -> float | None:
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 0:
        return gross_profit / gross_loss
    if gross_profit > 0:
        return float("inf")
    return None


async def _build_strategy_conversion_funnel(
    db: AsyncSession,
    since: datetime,
) -> dict:
    """
    Build strategy conversion funnel:
      signals_generated -> signals_passed_filter -> trades_executed -> close_count
    """
    sig_result = await db.execute(
        select(TradeSignal).where(TradeSignal.created_at >= since)
    )
    signals = sig_result.scalars().all()

    # Strategy map by signal id
    strategy_of_signal: dict = {}
    funnel: dict[str, dict] = {}
    for s in signals:
        sid = s.id
        strat = s.strategy or "unknown"
        strategy_of_signal[sid] = strat
        if strat not in funnel:
            funnel[strat] = {
                "signals_generated": 0,
                "decisions_made": 0,
                "signals_passed_filter": 0,
                "trades_executed": 0,
                "close_count": 0,
                "avg_pnl_per_trade": None,
                "pf": None,
                "conversion_signal_to_accept": None,
                "conversion_accept_to_trade": None,
                "conversion_trade_to_close": None,
            }
        funnel[strat]["signals_generated"] += 1

    dec_result = await db.execute(
        select(SignalDecision).where(SignalDecision.decided_at >= since)
    )
    decisions = dec_result.scalars().all()
    for d in decisions:
        strat = strategy_of_signal.get(d.signal_id)
        if not strat:
            continue
        funnel[strat]["decisions_made"] += 1
        if (d.decision or "").lower() == "accept":
            funnel[strat]["signals_passed_filter"] += 1

    ord_result = await db.execute(
        select(PaperOrder.signal_id, TradeSignal.strategy)
        .join(TradeSignal, TradeSignal.id == PaperOrder.signal_id)
        .where(
            PaperOrder.created_at >= since,
            PaperOrder.signal_id.isnot(None),
        )
    )
    executed_seen: set[tuple[str, str]] = set()
    for signal_id, strat in ord_result.all():
        key = (str(signal_id), strat or "unknown")
        # one trade per signal per strategy in funnel math
        if key in executed_seen:
            continue
        executed_seen.add(key)
        strategy = strat or "unknown"
        if strategy not in funnel:
            funnel[strategy] = {
                "signals_generated": 0,
                "decisions_made": 0,
                "signals_passed_filter": 0,
                "trades_executed": 0,
                "close_count": 0,
                "avg_pnl_per_trade": None,
                "pf": None,
                "conversion_signal_to_accept": None,
                "conversion_accept_to_trade": None,
                "conversion_trade_to_close": None,
            }
        funnel[strategy]["trades_executed"] += 1

    pos_result = await db.execute(
        select(PaperPosition).where(
            PaperPosition.status == "closed",
            PaperPosition.closed_at >= since,
        )
    )
    closed_positions = pos_result.scalars().all()
    pnl_by_strategy: dict[str, list[float]] = {}
    for p in closed_positions:
        strat = p.strategy or "unknown"
        if strat not in funnel:
            funnel[strat] = {
                "signals_generated": 0,
                "decisions_made": 0,
                "signals_passed_filter": 0,
                "trades_executed": 0,
                "close_count": 0,
                "avg_pnl_per_trade": None,
                "pf": None,
                "conversion_signal_to_accept": None,
                "conversion_accept_to_trade": None,
                "conversion_trade_to_close": None,
            }
        funnel[strat]["close_count"] += 1
        pnl_by_strategy.setdefault(strat, []).append(float(p.realized_pnl or 0))

    for strat, row in funnel.items():
        pnl_values = pnl_by_strategy.get(strat, [])
        if pnl_values:
            row["avg_pnl_per_trade"] = round(sum(pnl_values) / len(pnl_values), 4)
            pf = _safe_pf(pnl_values)
            row["pf"] = round(pf, 4) if pf not in (None, float("inf")) else None
        sg = row["signals_generated"]
        sa = row["signals_passed_filter"]
        te = row["trades_executed"]
        cc = row["close_count"]
        row["conversion_signal_to_accept"] = round(sa / sg, 4) if sg else None
        row["conversion_accept_to_trade"] = round(te / sa, 4) if sa else None
        row["conversion_trade_to_close"] = round(cc / te, 4) if te else None

    by_strategy = [
        {"strategy": strat, **vals}
        for strat, vals in sorted(funnel.items(), key=lambda x: x[0])
    ]
    totals = {
        "signals_generated": sum(r["signals_generated"] for r in by_strategy),
        "decisions_made": sum(r["decisions_made"] for r in by_strategy),
        "signals_passed_filter": sum(r["signals_passed_filter"] for r in by_strategy),
        "trades_executed": sum(r["trades_executed"] for r in by_strategy),
        "close_count": sum(r["close_count"] for r in by_strategy),
    }
    totals["conversion_signal_to_accept"] = (
        round(totals["signals_passed_filter"] / totals["signals_generated"], 4)
        if totals["signals_generated"] else None
    )
    totals["conversion_accept_to_trade"] = (
        round(totals["trades_executed"] / totals["signals_passed_filter"], 4)
        if totals["signals_passed_filter"] else None
    )
    totals["conversion_trade_to_close"] = (
        round(totals["close_count"] / totals["trades_executed"], 4)
        if totals["trades_executed"] else None
    )
    return {"since": since.isoformat(), "totals": totals, "by_strategy": by_strategy}


async def _build_reject_pareto(db: AsyncSession, since: datetime) -> dict:
    """Reject reason Pareto for entry-filter diagnostics."""
    result = await db.execute(
        select(SignalDecision.reject_reason)
        .where(
            SignalDecision.decided_at >= since,
            SignalDecision.decision == "reject",
            SignalDecision.reject_reason.isnot(None),
        )
    )
    rows = [r[0] for r in result.all() if r[0]]
    counts: dict[str, int] = {}
    for rr in rows:
        key = str(rr).split(":")[0].strip()
        counts[key] = counts.get(key, 0) + 1
    total = sum(counts.values())
    pareto = sorted(
        [
            {
                "reason_code": k,
                "count": v,
                "share": round(v / total, 4) if total else 0.0,
            }
            for k, v in counts.items()
        ],
        key=lambda x: -x["count"],
    )
    dominant = pareto[0] if pareto else None
    return {
        "total_rejects": total,
        "dominant_reason": dominant["reason_code"] if dominant else None,
        "dominant_reason_share": dominant["share"] if dominant else None,
        "pareto": pareto[:10],
    }


async def _build_tradable_edge_stats(db: AsyncSession, since: datetime) -> dict:
    """Tradable/executable edge distribution from signal costs_breakdown."""
    result = await db.execute(
        select(TradeSignal).where(TradeSignal.created_at >= since)
    )
    signals = result.scalars().all()
    if not signals:
        return {
            "sample_size": 0,
            "positive_executable_share": None,
            "negative_or_zero_executable_share": None,
            "avg_executable_edge": None,
            "avg_confidence_adjusted_edge": None,
        }
    exec_edges: list[float] = []
    conf_edges: list[float] = []
    for s in signals:
        cb = s.costs_breakdown or {}
        exec_edge = cb.get("executable_edge")
        conf_edge = cb.get("confidence_adjusted_edge")
        if conf_edge is None and s.net_edge is not None and s.model_confidence is not None:
            conf_edge = float(s.net_edge) * float(s.model_confidence)
        if exec_edge is None:
            net_edge = float(s.net_edge or 0)
            conf = float(s.model_confidence or 0.5)
            liq = float(cb.get("liquidity_factor", 0.8))
            fp = float(cb.get("fill_probability", 0.6))
            exec_edge = net_edge * conf * liq * fp
        if exec_edge is not None:
            exec_edges.append(float(exec_edge))
        if conf_edge is not None:
            conf_edges.append(float(conf_edge))
    if not exec_edges:
        return {
            "sample_size": len(signals),
            "positive_executable_share": None,
            "negative_or_zero_executable_share": None,
            "avg_executable_edge": None,
            "executable_edge_std": None,
            "executable_edge_variance": None,
            "avg_confidence_adjusted_edge": round(sum(conf_edges) / len(conf_edges), 6) if conf_edges else None,
        }
    positives = sum(1 for e in exec_edges if e > 0)
    total = len(exec_edges)
    avg_exec = sum(exec_edges) / total
    variance = sum((e - avg_exec) ** 2 for e in exec_edges) / total if total else 0
    std_exec = variance ** 0.5
    return {
        "sample_size": total,
        "positive_executable_share": round(positives / total, 4),
        "negative_or_zero_executable_share": round((total - positives) / total, 4),
        "avg_executable_edge": round(avg_exec, 6),
        "executable_edge_std": round(std_exec, 6),
        "executable_edge_variance": round(variance, 8),
        "avg_confidence_adjusted_edge": round(sum(conf_edges) / len(conf_edges), 6) if conf_edges else None,
    }


async def _build_wallet_concentration_metrics(
    db: AsyncSession,
    since: datetime,
    strategies: tuple[str, ...] = ("direct_copy", "high_conviction"),
) -> dict:
    """
    Wallet concentration KPIs for A/B test: wallet influence reduction.
    - n_wallets_contributed: distinct wallets with closed trades
    - top_5_wallet_share: fraction of closed trades from top 5 wallets
    - direct_copy_pnl_variance: variance of realized_pnl for direct_copy
    """
    result = await db.execute(
        select(
            PaperPosition.strategy,
            PaperPosition.source_wallet_id,
            PaperPosition.realized_pnl,
        ).where(
            PaperPosition.status == "closed",
            PaperPosition.closed_at >= since,
            PaperPosition.strategy.in_(strategies),
        )
    )
    rows = result.all()
    out: dict = {}
    for strat in strategies:
        strat_rows = [(r[1], r[2]) for r in rows if r[0] == strat]
        if not strat_rows:
            out[strat] = {
                "n_wallets_contributed": 0,
                "top_5_wallet_share": None,
                "pnl_variance": None,
                "pnl_std": None,
                "close_count": 0,
            }
            continue
        wallet_counts: dict[str | None, int] = {}
        pnl_values: list[float] = []
        for w, pnl in strat_rows:
            key = str(w) if w else None
            wallet_counts[key] = wallet_counts.get(key, 0) + 1
            pnl_values.append(float(pnl or 0))
        n_wallets = len([k for k in wallet_counts if k])
        total_trades = len(strat_rows)
        top5_count = sum(
            c for _, c in sorted(wallet_counts.items(), key=lambda x: -x[1])[:5]
        )
        top_5_share = round(top5_count / total_trades, 4) if total_trades else None
        if len(pnl_values) >= 2:
            avg_pnl = sum(pnl_values) / len(pnl_values)
            var_pnl = sum((p - avg_pnl) ** 2 for p in pnl_values) / len(pnl_values)
            std_pnl = var_pnl ** 0.5
        else:
            var_pnl = None
            std_pnl = None
        out[strat] = {
            "n_wallets_contributed": n_wallets,
            "top_5_wallet_share": top_5_share,
            "pnl_variance": round(var_pnl, 8) if var_pnl is not None else None,
            "pnl_std": round(std_pnl, 4) if std_pnl is not None else None,
            "close_count": total_trades,
        }
    return out


async def _build_calibration_kpis(db: AsyncSession) -> dict:
    calibrator = HybridProbabilityCalibrator()
    return await calibrator.diagnostics(db)


@router.get("/validation-cutoff-suggestion")
async def get_validation_cutoff_suggestion() -> dict:
    """
    Call right after backend restart + healthcheck to get exact cutoff for .env.
    Set VALIDATION_CUTOFF_AT to the returned value.
    """
    now = datetime.now(timezone.utc)
    return {
        "suggested_cutoff": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "instruction": "Add to .env: VALIDATION_CUTOFF_AT=" + now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


@router.get("/validation-report")
async def get_validation_report(
    cutoff_hours: int = Query(48, ge=1, le=168, description="Hours since cutoff for 'after' window (default 48h post-patch)"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Post-patch validation: trading vs infra exits, before/after comparison.

    Use VALIDATION_CUTOFF_AT env (ISO datetime) to split before/after.
    If not set, uses last N hours as 'after' and previous N hours as 'before'.

    Key metrics:
    - trading_exits: real signal-quality exits (target_hit, stop_loss, etc.)
    - infra_exits: stale_data, position_cap_cleanup, demo_cleanup
    - stale_data share: should drop post-patch
    - PF on trading_exits only: target > 1.0
    """
    now = datetime.now(timezone.utc)
    cutoff, from_env = _resolve_validation_cutoff(cutoff_hours)

    before_start = cutoff - timedelta(hours=cutoff_hours)
    before_end = cutoff
    after_start = cutoff
    after_end = now

    result = await db.execute(
        select(PaperPosition).where(
            PaperPosition.status == "closed",
            PaperPosition.closed_at.isnot(None),
        )
    )
    all_pos = result.scalars().all()

    def _classify(reason: str | None) -> str:
        r = reason or "unknown"
        if r in TRADING_EXITS:
            return "trading"
        if r in INFRA_EXITS:
            return "infra"
        return "other"

    def _stats(pos_list: list) -> dict:
        if not pos_list:
            return {"count": 0, "gross_pnl": 0, "net_pnl": 0, "fees": 0, "slippage": 0,
                    "target_hit": 0, "stop_loss": 0, "stale_data": 0, "pf": None, "win_rate": None,
                    "avg_pnl_per_trade": None}
        wins = [p for p in pos_list if (p.realized_pnl or 0) > 0]
        losses = [p for p in pos_list if (p.realized_pnl or 0) <= 0]
        gross = sum(float(p.realized_pnl or 0) for p in pos_list)
        fees = sum(float(p.total_fees or 0) for p in pos_list)
        slip = sum(float(p.total_slippage or 0) for p in pos_list)
        tw = sum(float(p.realized_pnl or 0) for p in wins)
        tl = abs(sum(float(p.realized_pnl or 0) for p in losses))
        pf = tw / tl if tl > 0 else (None if tw == 0 else float("inf"))
        return {
            "count": len(pos_list),
            "gross_pnl": round(gross, 4),
            "net_pnl": round(gross - fees - slip, 4),
            "fees": round(fees, 4),
            "slippage": round(slip, 4),
            "target_hit": sum(1 for p in pos_list if p.exit_reason == "target_hit"),
            "stop_loss": sum(1 for p in pos_list if p.exit_reason == "stop_loss"),
            "stale_data": sum(1 for p in pos_list if p.exit_reason == "stale_data"),
            "position_cap_cleanup": sum(1 for p in pos_list if p.exit_reason == "position_cap_cleanup"),
            "pf": round(pf, 4) if pf and pf != float("inf") else None,
            "win_rate": round(len(wins) / len(pos_list), 4) if pos_list else None,
            "avg_pnl_per_trade": round(gross / len(pos_list), 4) if pos_list else None,
        }

    def _avg_hold_hours(pos_list: list) -> float | None:
        if not pos_list:
            return None
        hours = []
        for p in pos_list:
            if p.opened_at and p.closed_at:
                h = (p.closed_at.replace(tzinfo=timezone.utc) - p.opened_at.replace(tzinfo=timezone.utc)).total_seconds() / 3600
                hours.append(h)
        return round(sum(hours) / len(hours), 2) if hours else None

    pos_before = [p for p in all_pos if p.closed_at and before_start <= p.closed_at < before_end]
    pos_after = [p for p in all_pos if p.closed_at and after_start <= p.closed_at <= after_end]

    # Avg spread at entry (from signal_decisions via paper_orders)
    from sqlalchemy import text
    async def _avg_spread_for_range(start, end, inclusive_end: bool = False) -> float | None:
        cond = "pp.closed_at <= :end" if inclusive_end else "pp.closed_at < :end"
        r = await db.execute(text(f"""
            SELECT AVG(sd.spread_at_decision) AS avg_spread
            FROM paper_positions pp
            JOIN paper_orders po ON po.market_id = pp.market_id
              AND po.created_at BETWEEN pp.opened_at - INTERVAL '5 seconds'
                                    AND pp.opened_at + INTERVAL '5 seconds'
            JOIN signal_decisions sd ON sd.signal_id = po.signal_id AND sd.decision = 'accept'
            WHERE pp.status = 'closed' AND pp.closed_at >= :start AND {cond}
              AND sd.spread_at_decision IS NOT NULL
        """), {"start": start, "end": end})
        row = r.mappings().one_or_none()
        return round(float(row["avg_spread"]), 4) if row and row["avg_spread"] is not None else None

    avg_spread_before = await _avg_spread_for_range(before_start, before_end)
    avg_spread_after = await _avg_spread_for_range(after_start, after_end, inclusive_end=True)

    trading_before = [p for p in pos_before if _classify(p.exit_reason) == "trading"]
    trading_after = [p for p in pos_after if _classify(p.exit_reason) == "trading"]
    infra_before = [p for p in pos_before if _classify(p.exit_reason) == "infra"]
    infra_after = [p for p in pos_after if _classify(p.exit_reason) == "infra"]

    def _build_alarm_checks(pb, pa, ta, st, asp_before, asp_after):
        sb, sa = st(pb), st(pa)
        ta_st = st(ta) if ta else {}
        real_share_before = len([p for p in pb if _classify(p.exit_reason) == "trading"]) / len(pb) if pb else 0
        real_share_after = len([p for p in pa if _classify(p.exit_reason) == "trading"]) / len(pa) if pa else 0
        fee_slip_before = (sb["fees"] + sb["slippage"]) / abs(sb["gross_pnl"]) if pb and sb["gross_pnl"] != 0 else None
        fee_slip_after = (sa["fees"] + sa["slippage"]) / abs(sa["gross_pnl"]) if pa and sa["gross_pnl"] != 0 else None
        return {
            "stale_data_share_dropped": (sa["stale_data"] / len(pa) < sb["stale_data"] / len(pb))
            if pb and pa else None,
            "trading_pf_above_1": (ta_st.get("pf") or 0) >= 1.0 if ta else None,
            "target_hit_gt_stop_loss": ta_st.get("target_hit", 0) > ta_st.get("stop_loss", 0) if ta else None,
            "avg_entry_spread_below_0_03": (asp_after is not None and asp_after < 0.03) if asp_after is not None else None,
            "fee_plus_slippage_share_improved": (
                fee_slip_after is not None and fee_slip_before is not None and fee_slip_after < fee_slip_before
            ),
            "real_trade_share_improved": real_share_after > real_share_before if pb and pa else None,
        }

    # Hold time buckets (for after only)
    hold_buckets = {"0_2h": [], "2_6h": [], "6_24h": [], "24h_plus": []}
    for p in pos_after:
        if p.opened_at and p.closed_at:
            h = (p.closed_at.replace(tzinfo=timezone.utc) - p.opened_at.replace(tzinfo=timezone.utc)).total_seconds() / 3600
            if h < 2:
                hold_buckets["0_2h"].append(p)
            elif h < 6:
                hold_buckets["2_6h"].append(p)
            elif h < 24:
                hold_buckets["6_24h"].append(p)
            else:
                hold_buckets["24h_plus"].append(p)

    sb, sa = _stats(pos_before), _stats(pos_after)

    return {
        "generated_at": now.isoformat(),
        "cutoff": cutoff.isoformat(),
        "cutoff_source": "env" if from_env else f"fallback_last_{cutoff_hours}h",
        "windows": {
            "before": {"start": before_start.isoformat(), "end": before_end.isoformat()},
            "after": {"start": after_start.isoformat(), "end": after_end.isoformat()},
        },
        "summary": {
            "total_closed_before": len(pos_before),
            "total_closed_after": len(pos_after),
            "trading_closed_before": len(trading_before),
            "trading_closed_after": len(trading_after),
            "infra_closed_before": len(infra_before),
            "infra_closed_after": len(infra_after),
            "fee_before": sb["fees"],
            "fee_after": sa["fees"],
            "slippage_before": sb["slippage"],
            "slippage_after": sa["slippage"],
            "avg_spread_entry_before": avg_spread_before,
            "avg_spread_entry_after": avg_spread_after,
            "avg_hold_hours_before": _avg_hold_hours(pos_before),
            "avg_hold_hours_after": _avg_hold_hours(pos_after),
        },
        "before_patch": {
            "total": sb,
            "trading_only": _stats(trading_before),
            "infra_only": _stats(infra_before),
            "stale_data_share": round(len(infra_before) / len(pos_before), 4) if pos_before else None,
        },
        "after_patch": {
            "total": sa,
            "trading_only": _stats(trading_after),
            "infra_only": _stats(infra_after),
            "stale_data_share": round(len([p for p in pos_after if p.exit_reason == "stale_data"]) / len(pos_after), 4) if pos_after else None,
        },
        "hold_time_buckets_after": {
            k: _stats(v) for k, v in hold_buckets.items()
        },
        "alarm_checks": _build_alarm_checks(pos_before, pos_after, trading_after, _stats, avg_spread_before, avg_spread_after),
        "exit_classification": {
            "trading": list(TRADING_EXITS),
            "infra": list(INFRA_EXITS),
        },
    }


@router.get("/strategy-conversion-funnel")
async def get_strategy_conversion_funnel(
    lookback_hours: int = Query(48, ge=1, le=168),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Signals -> accepts -> executed trades -> closed trades conversion funnel."""
    since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    funnel = await _build_strategy_conversion_funnel(db, since=since)
    reject_pareto = await _build_reject_pareto(db, since=since)
    edge_stats = await _build_tradable_edge_stats(db, since=since)
    wallet_concentration = await _build_wallet_concentration_metrics(db, since=since)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lookback_hours": lookback_hours,
        "reject_pareto": reject_pareto,
        "tradable_edge_stats": edge_stats,
        "wallet_concentration": wallet_concentration,
        **funnel,
    }


@router.get("/baseline-snapshot")
async def get_baseline_snapshot(
    cutoff_hours: int = Query(48, ge=1, le=168),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Single-shot baseline pack for 6-hour monitoring loop.
    Includes 3 vitals + alarm checks + conversion funnel.
    """
    validation = await get_validation_report(cutoff_hours=cutoff_hours, db=db)
    stale = await get_stale_data_analytics(db=db)
    strategy_health = await get_strategy_health(db=db)
    since = datetime.now(timezone.utc) - timedelta(hours=cutoff_hours)
    funnel = await _build_strategy_conversion_funnel(db, since=since)
    calibration = await _build_calibration_kpis(db)
    reject_pareto = await _build_reject_pareto(db, since=since)
    tradable_edge_stats = await _build_tradable_edge_stats(db, since=since)

    after_trading = (validation.get("after_patch", {}) or {}).get("trading_only", {}) or {}
    after_total = (validation.get("after_patch", {}) or {}).get("total", {}) or {}
    before_stale_share = (validation.get("before_patch", {}) or {}).get("stale_data_share")
    after_stale_share = (validation.get("after_patch", {}) or {}).get("stale_data_share")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cutoff": validation.get("cutoff"),
        "cutoff_source": validation.get("cutoff_source"),
        "vitals": {
            "trading_only_pf": after_trading.get("pf"),
            "expectancy_proxy_avg_pnl_per_trade": after_trading.get("avg_pnl_per_trade"),
            "stale_data_share": after_stale_share,
            "stale_share_delta_vs_before": (
                round(after_stale_share - before_stale_share, 4)
                if isinstance(before_stale_share, (int, float)) and isinstance(after_stale_share, (int, float))
                else None
            ),
        },
        "alarm_checks": validation.get("alarm_checks", {}),
        "trading_after": after_trading,
        "total_after": after_total,
        "strategy_conversion_funnel": funnel,
        "reject_pareto": reject_pareto,
        "calibration_kpis": calibration,
        "tradable_edge_stats": tradable_edge_stats,
        "wallet_concentration": await _build_wallet_concentration_metrics(db, since=since),
        "strategy_health_summary": strategy_health.get("summary", {}),
        "stale_data_analytics": {
            "total_stale_exits": stale.get("total_stale_exits"),
            "total_pnl_impact": stale.get("total_pnl_impact"),
            "top_markets_by_stale_count": stale.get("top_markets_by_stale_count", [])[:10],
        },
    }


@router.get("/stale-phase-status")
async def get_stale_phase_status(
    phase: str = Query("A", pattern="^[ABCabc]$"),
    cutoff_hours: int = Query(48, ge=1, le=168),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Phase compliance check for stale guard rollout:
    A=soft_guard only, B=stale_exit_disabled test, C=blacklist local surgery.
    """
    settings = get_settings()
    phase_u = phase.upper()
    expected = {
        "A": {"STALE_SOFT_GUARD": True, "STALE_EXIT_DISABLED": False, "blacklist_required": False},
        "B": {"STALE_SOFT_GUARD": True, "STALE_EXIT_DISABLED": True, "blacklist_required": False},
        "C": {"STALE_SOFT_GUARD": True, "STALE_EXIT_DISABLED": False, "blacklist_required": True},
    }[phase_u]

    blacklist_count = len([s for s in (settings.STALE_MARKET_BLACKLIST or "").split(",") if s.strip()])
    checks = {
        "soft_guard_ok": settings.STALE_SOFT_GUARD == expected["STALE_SOFT_GUARD"],
        "stale_exit_disabled_ok": settings.STALE_EXIT_DISABLED == expected["STALE_EXIT_DISABLED"],
        "blacklist_ok": (blacklist_count > 0) if expected["blacklist_required"] else (blacklist_count == 0),
    }
    validation = await get_validation_report(cutoff_hours=cutoff_hours, db=db)
    open_count = await db.scalar(select(func.count()).where(PaperPosition.status == "open")) or 0
    compliant = all(checks.values())

    return {
        "phase": phase_u,
        "compliant": compliant,
        "checks": checks,
        "current_config": {
            "STALE_SOFT_GUARD": settings.STALE_SOFT_GUARD,
            "STALE_EXIT_DISABLED": settings.STALE_EXIT_DISABLED,
            "STALE_MARKET_BLACKLIST_count": blacklist_count,
            "STALE_SNAPSHOT_HOURS": settings.STALE_SNAPSHOT_HOURS,
        },
        "health_signals": {
            "stale_data_share_after": ((validation.get("after_patch") or {}).get("stale_data_share")),
            "infra_closed_after": ((validation.get("summary") or {}).get("infra_closed_after")),
            "open_positions": open_count,
        },
    }


@router.get("/entry-single-knob")
async def get_entry_single_knob(
    lookback_hours: int = Query(72, ge=1, le=240),
    db: AsyncSession = Depends(get_db),
) -> dict:
    import os

    """
    Suggest exactly one entry knob based on dominant reject reason Pareto.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    pareto_data = await _build_reject_pareto(db, since=since)
    total_rejects = pareto_data["total_rejects"]
    dominant = pareto_data["dominant_reason"]
    dominant_share = pareto_data["dominant_reason_share"]
    pareto = [
        {"reject_reason": p["reason_code"], "count": p["count"], "share": p["share"]}
        for p in pareto_data["pareto"]
    ]

    # Single-knob mapping
    param = None
    direction = None
    rationale = "Insufficient reject data."
    if dominant:
        d = dominant.lower()
        if "spread" in d:
            param = "MAX_SPREAD_ABS"
            direction = "increase_slightly"
            rationale = "Dominant blocker spread-related; widen spread gate slightly as single-knob test."
        elif "conf_edge" in d or "edge" in d:
            param = "MIN_CONF_EDGE"
            direction = "decrease_slightly"
            rationale = "Dominant blocker confidence-edge related; lower min confidence edge slightly as single-knob test."
        elif "liquidity" in d:
            param = "MIN_DEPTH_USD"
            direction = "decrease_slightly"
            rationale = "Dominant blocker liquidity; lower minimum depth slightly as single-knob test."
        else:
            param = "MAX_SPREAD_ABS"
            direction = "no_change"
            rationale = "No clear mapping; keep knobs unchanged until more rejects accumulate."

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lookback_hours": lookback_hours,
        "total_rejects": total_rejects,
        "reject_pareto": pareto[:10],
        "dominant_reject_reason": dominant,
        "dominant_reject_reason_share": dominant_share,
        "current_entry_knobs": {
            "MIN_CONF_EDGE": float(os.getenv("MIN_CONF_EDGE", "0.012")),
            "MAX_SPREAD_ABS": float(os.getenv("MAX_SPREAD_ABS", "0.03")),
        },
        "single_knob_policy": {
            "parameter": param,
            "direction": direction,
            "rationale": rationale,
            "rule": "Change only one parameter per iteration.",
            "violation_detected": (
                float(os.getenv("MIN_CONF_EDGE", "0.012")) != 0.012
                and float(os.getenv("MAX_SPREAD_ABS", "0.03")) != 0.03
            ),
        },
    }


@router.get("/decision-gate-72h")
async def get_decision_gate_72h(
    cutoff_hours: int = Query(72, ge=24, le=240),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Keep / Iterate / Rollback decision endpoint for the first 72h gate.
    """
    validation = await get_validation_report(cutoff_hours=cutoff_hours, db=db)
    settings = get_settings()
    since = datetime.now(timezone.utc) - timedelta(hours=cutoff_hours)
    funnel = await _build_strategy_conversion_funnel(db, since=since)
    calibration = await _build_calibration_kpis(db)
    edge_stats = await _build_tradable_edge_stats(db, since=since)
    wallet_concentration = await _build_wallet_concentration_metrics(db, since=since)
    after_patch = validation.get("after_patch", {}) or {}
    before_patch = validation.get("before_patch", {}) or {}
    trading_after = after_patch.get("trading_only", {}) or {}
    stale_before = before_patch.get("stale_data_share")
    stale_after = after_patch.get("stale_data_share")
    alarm_checks = validation.get("alarm_checks", {}) or {}

    pf = trading_after.get("pf")
    expectancy = trading_after.get("avg_pnl_per_trade")
    open_positions = await db.scalar(select(func.count()).where(PaperPosition.status == "open")) or 0
    stale_dropped = (
        isinstance(stale_before, (int, float))
        and isinstance(stale_after, (int, float))
        and stale_after < stale_before
    )

    keep_conditions = {
        "pf_ge_1": (pf is not None and pf >= 1.0),
        "expectancy_positive": (expectancy is not None and expectancy > 0),
        "stale_share_dropped": stale_dropped,
        "stop_loss_stabilized_0_10": abs(float(settings.STOP_LOSS_PCT) - 0.10) < 1e-9,
        "open_positions_controlled": open_positions <= 50,
        "alarms_majority_green": sum(1 for v in alarm_checks.values() if v is True) >= 4,
        "calibration_healthy": (
            calibration.get("ece") is None
            or float(calibration.get("ece")) <= 0.12
        ),
        "executable_edge_positive_share_ok": (
            edge_stats.get("positive_executable_share") is None
            or float(edge_stats.get("positive_executable_share")) >= 0.5
        ),
    }
    keep = all(keep_conditions.values())

    rollback_conditions = {
        "stale_not_improved": (not stale_dropped),
        "expectancy_negative": (expectancy is not None and expectancy < 0),
        "open_positions_risky": open_positions > 100,
    }
    rollback = sum(1 for v in rollback_conditions.values() if v) >= 2

    if keep:
        verdict = "keep_and_scale"
    elif rollback:
        verdict = "rollback"
    else:
        verdict = "iterate_filters"

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_hours": cutoff_hours,
        "verdict": verdict,
        "keep_conditions": keep_conditions,
        "rollback_conditions": rollback_conditions,
        "key_metrics": {
            "trading_only_pf": pf,
            "expectancy": expectancy,
            "stale_data_share_before": stale_before,
            "stale_data_share_after": stale_after,
            "stop_loss_pct": float(settings.STOP_LOSS_PCT),
            "open_positions": open_positions,
            "alarm_checks": alarm_checks,
            "calibration_kpis": calibration,
            "tradable_edge_stats": edge_stats,
            "wallet_concentration": wallet_concentration,
            "funnel_totals": funnel.get("totals", {}),
        },
    }


@router.get("/phase-decision-script")
async def get_phase_decision_script(
    cutoff_hours: int = Query(72, ge=24, le=240),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Decision support script output (report-only, no automatic config changes).

    Produces:
      1) phase decision: CONTINUE / WATCH / ROLLBACK_CANDIDATE
      2) decision reasons (human-readable)
      3) single next recommendation
      4) veto flags
      5) top dashboard metrics (9-line compact view)
    """
    validation = await get_validation_report(cutoff_hours=cutoff_hours, db=db)
    since = datetime.now(timezone.utc) - timedelta(hours=cutoff_hours)
    funnel = await _build_strategy_conversion_funnel(db, since=since)
    reject_pareto = await _build_reject_pareto(db, since=since)
    calibration = await _build_calibration_kpis(db)
    edge_stats = await _build_tradable_edge_stats(db, since=since)
    wallet_concentration = await _build_wallet_concentration_metrics(db, since=since)

    before_trading = ((validation.get("before_patch") or {}).get("trading_only") or {})
    after_trading = ((validation.get("after_patch") or {}).get("trading_only") or {})
    stale_before = ((validation.get("before_patch") or {}).get("stale_data_share"))
    stale_after = ((validation.get("after_patch") or {}).get("stale_data_share"))
    open_positions = await db.scalar(select(func.count()).where(PaperPosition.status == "open")) or 0

    pf_before = before_trading.get("pf")
    pf_after = after_trading.get("pf")
    exp_before = before_trading.get("avg_pnl_per_trade")
    exp_after = after_trading.get("avg_pnl_per_trade")

    veto_flags = {
        "stale_share_worse": (
            isinstance(stale_before, (int, float))
            and isinstance(stale_after, (int, float))
            and stale_after > stale_before
        ),
        "trading_only_pf_not_improved": (
            isinstance(pf_before, (int, float))
            and isinstance(pf_after, (int, float))
            and pf_after <= pf_before
        ),
        "avg_pnl_per_trade_not_improved": (
            isinstance(exp_before, (int, float))
            and isinstance(exp_after, (int, float))
            and exp_after <= exp_before
        ),
        "open_positions_risk": open_positions > 80,
    }

    # Reasons
    reasons: list[str] = []
    if isinstance(pf_before, (int, float)) and isinstance(pf_after, (int, float)):
        reasons.append(
            "trading_only_pf_improved" if pf_after > pf_before else "trading_only_pf_not_improved"
        )
    if isinstance(exp_before, (int, float)) and isinstance(exp_after, (int, float)):
        reasons.append(
            "expectancy_improved" if exp_after > exp_before else "expectancy_not_improved"
        )
    if isinstance(stale_before, (int, float)) and isinstance(stale_after, (int, float)):
        reasons.append(
            "stale_share_improved" if stale_after < stale_before else "stale_share_worse"
        )
    pos_exec_share = edge_stats.get("positive_executable_share")
    if isinstance(pos_exec_share, (int, float)):
        reasons.append(
            "positive_executable_share_healthy"
            if pos_exec_share >= 0.5
            else "positive_executable_share_low"
        )
    dominant_reject = reject_pareto.get("dominant_reason")
    if dominant_reject:
        reasons.append(f"reject_pareto_dominated_by_{dominant_reject}")
    if veto_flags["open_positions_risk"]:
        reasons.append("open_positions_risk_high")

    # Decision with veto logic
    hard_veto_count = sum(
        1
        for k in (
            "stale_share_worse",
            "trading_only_pf_not_improved",
            "avg_pnl_per_trade_not_improved",
        )
        if veto_flags[k]
    )
    if hard_veto_count >= 2:
        phase_decision = "ROLLBACK_CANDIDATE"
    elif hard_veto_count == 1 or veto_flags["open_positions_risk"]:
        phase_decision = "WATCH"
    else:
        phase_decision = "CONTINUE"

    # Single recommendation
    if phase_decision == "CONTINUE":
        next_recommendation = "Phase 2A continue"
    elif veto_flags["stale_share_worse"]:
        next_recommendation = "Move to stale disabled test"
    elif dominant_reject:
        next_recommendation = "Inspect top reject reason first"
    else:
        next_recommendation = "Keep current phase and monitor"

    # Top-9 compact dashboard lines + wallet concentration (A/B test KPIs)
    by_strat = {r["strategy"]: r for r in funnel.get("by_strategy", [])}
    dc_wallet = (wallet_concentration.get("direct_copy") or {})
    top9 = {
        "trading_only_pf": pf_after,
        "avg_pnl_per_trade": exp_after,
        "stale_data_share": stale_after,
        "positive_executable_share": pos_exec_share,
        "calibration_method": calibration.get("method"),
        "calibration_sample_size": calibration.get("sample_size"),
        "top_reject_reason": dominant_reject,
        "direct_copy_pf": (by_strat.get("direct_copy") or {}).get("pf"),
        "high_conviction_pf": (by_strat.get("high_conviction") or {}).get("pf"),
        "n_wallets_contributed": dc_wallet.get("n_wallets_contributed"),
        "top_5_wallet_share": dc_wallet.get("top_5_wallet_share"),
        "direct_copy_pnl_variance": dc_wallet.get("pnl_variance"),
        "executable_edge_std": edge_stats.get("executable_edge_std"),
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_hours": cutoff_hours,
        "phase_decision": phase_decision,
        "decision_reasons": reasons,
        "next_single_recommendation": next_recommendation,
        "veto_flags": veto_flags,
        "top_dashboard_metrics": top9,
        "wallet_concentration": wallet_concentration,
    }


@router.get("/position-cap-cleanup-verification")
async def get_position_cap_cleanup_verification(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Verify position_cap_cleanup: timestamp distribution and post-cutoff count.
    If count_after_cutoff == 0 → legacy (no new records). If > 0 → hayalet hâlâ evde.
    """
    import os

    cutoff_str = os.getenv("VALIDATION_CUTOFF_AT")
    cutoff = None
    if cutoff_str:
        try:
            cutoff = datetime.fromisoformat(cutoff_str.replace("Z", "+00:00"))
        except Exception:
            pass

    result = await db.execute(
        select(PaperPosition)
        .where(PaperPosition.exit_reason == "position_cap_cleanup")
        .order_by(PaperPosition.closed_at.desc())
    )
    positions = result.scalars().all()

    # Timestamp distribution: by date
    by_date: dict[str, int] = {}
    for p in positions:
        if p.closed_at:
            key = p.closed_at.strftime("%Y-%m-%d")
            by_date[key] = by_date.get(key, 0) + 1

    count_after_cutoff = sum(1 for p in positions if p.closed_at and cutoff and p.closed_at >= cutoff) if cutoff else None
    latest_closed = positions[0].closed_at.isoformat() if positions else None

    return {
        "total_position_cap_cleanup": len(positions),
        "count_after_cutoff": count_after_cutoff,
        "cutoff": cutoff.isoformat() if cutoff else None,
        "verdict": (
            "legacy" if count_after_cutoff == 0 and cutoff
            else "active" if count_after_cutoff and count_after_cutoff > 0
            else "unknown"
        ),
        "latest_closed_at": latest_closed,
        "by_date": dict(sorted(by_date.items(), key=lambda x: -x[1])),
    }


@router.get("/stale-data-analytics")
async def get_stale_data_analytics(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Aggregate stale_data exits: market distribution, strategy split.
    Note: snap_age_h per exit comes from logs; here we show counts and PnL impact.
    """
    result = await db.execute(
        select(PaperPosition)
        .where(PaperPosition.exit_reason == "stale_data", PaperPosition.status == "closed")
    )
    positions = result.scalars().all()

    by_market: dict[str, dict] = {}
    by_strategy: dict[str, int] = {}
    total_pnl = 0.0

    for p in positions:
        mid = str(p.market_id)
        if mid not in by_market:
            by_market[mid] = {"count": 0, "total_pnl": 0.0}
        by_market[mid]["count"] += 1
        by_market[mid]["total_pnl"] += float(p.realized_pnl or 0)

        strat = p.strategy or "unknown"
        by_strategy[strat] = by_strategy.get(strat, 0) + 1
        total_pnl += float(p.realized_pnl or 0)

    # Top markets by stale count (worst offenders)
    top_markets = sorted(
        [{"market_id": k, **v} for k, v in by_market.items()],
        key=lambda x: -x["count"],
    )[:20]

    # Suggested blacklist: top 10 for copy-paste into STALE_MARKET_BLACKLIST
    suggested_blacklist = ",".join(t["market_id"] for t in top_markets[:10])

    return {
        "total_stale_exits": len(positions),
        "total_pnl_impact": round(total_pnl, 4),
        "by_strategy": by_strategy,
        "top_markets_by_stale_count": top_markets,
        "market_count": len(by_market),
        "suggested_blacklist": suggested_blacklist,
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
