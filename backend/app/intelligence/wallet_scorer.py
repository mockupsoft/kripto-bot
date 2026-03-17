"""Multi-factor wallet scoring with copy_decay_curve computation."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trade import WalletTransaction
from app.models.wallet import Wallet, WalletScore
from app.models.market import MarketSnapshot


@dataclass
class ScoringResult:
    total_roi: float
    hit_rate: float
    avg_position_size: float
    avg_hold_time_seconds: int
    max_drawdown: float
    market_diversity_score: float
    concentration_penalty: float
    consistency_score: float
    suspiciousness_score: float
    classification: str
    copyability_score: float
    composite_score: float
    copy_decay_curve: dict[str, float]
    explanation: dict


async def score_wallet(db: AsyncSession, wallet_id) -> ScoringResult:
    result = await db.execute(
        select(WalletTransaction)
        .where(WalletTransaction.wallet_id == wallet_id)
        .order_by(WalletTransaction.occurred_at.asc())
    )
    txs = result.scalars().all()

    if not txs:
        return _empty_score()

    prices = [float(t.price) for t in txs if t.price]
    sizes = [float(t.size) for t in txs if t.size]
    notionals = [float(t.notional) for t in txs if t.notional]
    detection_lags = [t.detection_lag_ms for t in txs if t.detection_lag_ms is not None]

    total_trades = len(txs)
    buy_trades = [t for t in txs if t.side == "BUY"]
    sell_trades = [t for t in txs if t.side == "SELL"]

    profits = _estimate_profits(txs)
    wins = [p for p in profits if p > 0]
    losses = [p for p in profits if p <= 0]

    # ROI: cap denominator to prevent penny-position inflation
    # If a wallet trades at <0.05, notional is tiny → ROI explodes artificially.
    # Use max(sum(notionals), MIN_NOTIONAL_FLOOR) to dampen this.
    MIN_NOTIONAL_FLOOR = 1.0   # $1 minimum notional floor for ROI calculation
    total_roi = sum(profits) / max(sum(notionals) if notionals else 0, MIN_NOTIONAL_FLOOR)

    # hit_rate: only count non-trivial trades (price between 0.05–0.95)
    meaningful_profits = [p for p, t in zip(profits, txs)
                          if float(t.price or 0.5) >= 0.05 and float(t.price or 0.5) <= 0.95]
    if meaningful_profits:
        hit_rate = len([p for p in meaningful_profits if p > 0]) / len(meaningful_profits)
    else:
        hit_rate = 0.5  # no meaningful trades → neutral score
    avg_position_size = np.mean(sizes) if sizes else 0
    avg_hold = _avg_hold_time(txs)
    max_dd = _max_drawdown(profits)

    unique_markets = len(set(str(t.market_id) for t in txs))
    market_diversity = min(unique_markets / 10, 1.0)

    market_counts = {}
    for t in txs:
        market_counts[str(t.market_id)] = market_counts.get(str(t.market_id), 0) + 1
    max_concentration = max(market_counts.values()) / total_trades if market_counts else 0
    concentration_penalty = max_concentration

    # Consistency: std of per-trade returns (lower = more consistent)
    if len(profits) > 2:
        returns = [p / max(n, 1) for p, n in zip(profits, notionals)] if notionals else []
        consistency = 1.0 - min(np.std(returns) * 2, 1.0) if returns else 0.5
    else:
        consistency = 0.5

    suspiciousness = _suspiciousness(txs, profits)
    classification = _classify(hit_rate, total_roi, avg_hold, suspiciousness, len(txs))

    # Copy decay curve: estimate edge remaining at different latencies
    decay_curve = _compute_copy_decay(txs, prices, detection_lags)

    # Copyability: weighted average of decay at realistic latencies
    decay_values = list(decay_curve.values())
    copyability = np.mean(decay_values) if decay_values else 0
    copyability = max(0, min(1, copyability))

    # Composite: profitability * consistency * copyability - penalties
    raw_composite = (
        0.30 * min(max(total_roi + 0.5, 0), 1)
        + 0.20 * hit_rate
        + 0.25 * copyability
        + 0.15 * consistency
        - 0.05 * concentration_penalty
        - 0.05 * suspiciousness
    )
    composite = max(0, min(1, raw_composite))

    explanation = {
        "roi_contribution": round(0.30 * min(max(total_roi + 0.5, 0), 1), 4),
        "hit_rate_contribution": round(0.20 * hit_rate, 4),
        "copyability_contribution": round(0.25 * copyability, 4),
        "consistency_contribution": round(0.15 * consistency, 4),
        "concentration_penalty": round(0.05 * concentration_penalty, 4),
        "suspiciousness_penalty": round(0.05 * suspiciousness, 4),
        "trade_count": total_trades,
        "unique_markets": unique_markets,
        "classification_reason": classification,
    }

    return ScoringResult(
        total_roi=round(total_roi, 6),
        hit_rate=round(hit_rate, 4),
        avg_position_size=round(avg_position_size, 2),
        avg_hold_time_seconds=avg_hold,
        max_drawdown=round(max_dd, 4),
        market_diversity_score=round(market_diversity, 4),
        concentration_penalty=round(concentration_penalty, 4),
        consistency_score=round(consistency, 4),
        suspiciousness_score=round(suspiciousness, 4),
        classification=classification,
        copyability_score=round(copyability, 4),
        composite_score=round(composite, 4),
        copy_decay_curve={k: round(v, 4) for k, v in decay_curve.items()},
        explanation=explanation,
    )


async def score_and_persist(db: AsyncSession, wallet_id) -> WalletScore:
    result = await score_wallet(db, wallet_id)
    ws = WalletScore(
        wallet_id=wallet_id,
        total_roi=result.total_roi,
        hit_rate=result.hit_rate,
        avg_position_size=result.avg_position_size,
        avg_hold_time_seconds=result.avg_hold_time_seconds,
        max_drawdown=result.max_drawdown,
        market_diversity_score=result.market_diversity_score,
        concentration_penalty=result.concentration_penalty,
        consistency_score=result.consistency_score,
        suspiciousness_score=result.suspiciousness_score,
        classification=result.classification,
        copyability_score=result.copyability_score,
        composite_score=result.composite_score,
        copy_decay_curve=result.copy_decay_curve,
        explanation=result.explanation,
    )
    db.add(ws)
    await db.flush()

    # Keep only the 3 most recent score rows per wallet to prevent table bloat
    old_scores = await db.execute(
        select(WalletScore)
        .where(WalletScore.wallet_id == wallet_id)
        .order_by(WalletScore.scored_at.desc())
        .offset(3)
    )
    for old in old_scores.scalars().all():
        await db.delete(old)

    return ws


def _estimate_profits(txs: list) -> list[float]:
    """Estimate per-trade profit using price-based heuristic.

    For very cheap positions (price < 0.05), profit is near-zero regardless
    of side — these are long-shot bets, not directional alpha.
    A BUY at 0.001 is NOT a win just because price < 0.55.
    """
    profits = []
    for t in txs:
        price = float(t.price) if t.price else 0.5
        size = float(t.size) if t.size else 0
        notional = float(t.notional) if t.notional else (price * size)

        # Long-shot positions (price < 0.05 or > 0.95): minimal edge
        # The market has near-certain outcome priced in — copy edge is negligible
        if price < 0.05 or price > 0.95:
            # Treat as near-zero expected profit; neither win nor loss heuristically
            profits.append(0.0)
            continue

        if t.side == "BUY":
            implied_profit = (0.55 - price) * size
        else:
            implied_profit = (price - 0.45) * size
        profits.append(implied_profit)
    return profits


def _avg_hold_time(txs: list) -> int:
    if len(txs) < 2:
        return 0
    times = [t.occurred_at for t in txs if t.occurred_at]
    if len(times) < 2:
        return 0
    deltas = [(times[i + 1] - times[i]).total_seconds() for i in range(len(times) - 1)]
    return int(np.mean(deltas)) if deltas else 0


def _max_drawdown(profits: list[float]) -> float:
    if not profits:
        return 0
    cumulative = np.cumsum(profits)
    peak = np.maximum.accumulate(cumulative)
    drawdown = (peak - cumulative) / np.maximum(peak, 1)
    return float(np.max(drawdown)) if len(drawdown) > 0 else 0


def _suspiciousness(txs: list, profits: list) -> float:
    """Flag wallets with patterns suggesting wash trading or manipulation."""
    score = 0.0
    if len(txs) < 5:
        return 0.3

    sizes = [float(t.size) for t in txs if t.size]
    if sizes:
        size_std = np.std(sizes) / (np.mean(sizes) + 1e-8)
        if size_std < 0.1:
            score += 0.3  # suspiciously uniform sizes

    if profits:
        win_streak = max_consecutive(profits, lambda p: p > 0)
        if win_streak > 10:
            score += 0.2

    return min(score, 1.0)


def max_consecutive(values: list, condition) -> int:
    streak = 0
    max_s = 0
    for v in values:
        if condition(v):
            streak += 1
            max_s = max(max_s, streak)
        else:
            streak = 0
    return max_s


def _classify(hit_rate: float, roi: float, avg_hold: int, suspiciousness: float, count: int) -> str:
    if count < 3:
        return "unknown"
    if suspiciousness > 0.4 and hit_rate > 0.8:
        return "market_maker"
    if roi > 0.3 and avg_hold < 120:
        return "arbitrageur"
    if hit_rate < 0.35 and roi < -0.1:
        return "gambler"
    return "unknown"


def _compute_copy_decay(txs: list, prices: list, detection_lags: list) -> dict[str, float]:
    """Estimate what fraction of edge remains at various copy latencies.

    Uses detection_lag_ms distribution to model how quickly price moves
    after the wallet trades, eroding the available edge.

    If no lag data: return pessimistic defaults (we don't know if copyable).
    """
    if not detection_lags or not prices:
        # No lag data — we can't assess copyability; use conservative defaults
        return {"200ms": 0.3, "500ms": 0.2, "1000ms": 0.1, "1500ms": 0.05}

    avg_price = np.mean(prices)
    avg_edge = abs(avg_price - 0.5) * 2  # rough edge proxy

    latency_points = {"200ms": 200, "500ms": 500, "1000ms": 1000, "1500ms": 1500}
    median_lag = np.median(detection_lags)

    curve = {}
    for label, ms in latency_points.items():
        # Edge decays exponentially with copy latency
        # Faster wallets (low median_lag) lose edge faster because the market catches up
        decay_rate = 0.002 if median_lag < 500 else 0.001
        remaining = math.exp(-decay_rate * ms) * avg_edge
        normalized = remaining / max(avg_edge, 0.01)
        curve[label] = max(-0.2, min(1.0, normalized))

    return curve


def _empty_score() -> ScoringResult:
    return ScoringResult(
        total_roi=0, hit_rate=0, avg_position_size=0, avg_hold_time_seconds=0,
        max_drawdown=0, market_diversity_score=0, concentration_penalty=0,
        consistency_score=0, suspiciousness_score=0, classification="unknown",
        copyability_score=0, composite_score=0,
        copy_decay_curve={"200ms": 0, "500ms": 0, "1000ms": 0, "1500ms": 0},
        explanation={},
    )
