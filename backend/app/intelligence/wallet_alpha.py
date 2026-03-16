"""Wallet Intelligence Engine — Copyable Alpha Score & Influence Graph.

Mathematical framework:

copyable_alpha_score = (
    w1 * timing_score          # trades before price moves
  + w2 * persistence_score     # alpha is repeatable, not luck
  + w3 * vol_adjusted_roi      # return per unit of outcome volatility
  + w4 * latency_survivability # edge survives realistic copy delay
  + w5 * drawdown_stability    # doesn't blow up between wins
  + w6 * information_impact    # trades measurably move prices
) - penalty_suspiciousness

Influence Graph:
  Nodes = wallets
  Directed edges = "Wallet A traded, then market moved, then Wallet B followed"
  Edge weight = (mean_lag_AB, correlation of trade outcomes)
  Leader score = in-degree 0 + consistently precedes price move
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market import MarketSnapshot
from app.models.trade import WalletTransaction
from app.models.wallet import Wallet


# ── Weight vector (tunable) ────────────────────────────────────────────────
_W_TIMING        = 0.22   # leads price moves
_W_PERSISTENCE   = 0.18   # alpha repeatable over rolling windows
_W_VOL_ROI       = 0.20   # Sharpe-like return quality
_W_LATENCY_SURV  = 0.18   # edge survives 800 ms copy lag
_W_DD_STABILITY  = 0.12   # drawdown doesn't spike
_W_INFO_IMPACT   = 0.10   # own trades move price (info leader)
# suspiciousness penalty applied afterwards


@dataclass
class CopyableAlphaResult:
    wallet_id: str
    copyable_alpha_score: float       # 0–1 final composite
    timing_score: float
    persistence_score: float
    vol_adjusted_roi: float
    latency_survivability: float
    drawdown_stability: float
    information_impact: float
    suspiciousness_penalty: float
    # sub-explanations
    timing_detail: dict[str, Any]
    influence_detail: dict[str, Any]
    alpha_decay_risk: str             # "low" / "medium" / "high" / "critical"
    decay_signal: str                 # human-readable warning
    recommendation: str               # "copy_now" / "monitor" / "avoid"


@dataclass
class InfluenceEdge:
    leader_wallet_id: str
    follower_wallet_id: str
    market_id: str
    mean_lag_seconds: float           # avg seconds from leader trade to follower trade
    min_lag_seconds: float            # fastest observed lead
    max_lag_seconds: float            # slowest observed lead
    trade_count: int                  # observations
    outcome_correlation: float        # fraction of times they agreed
    avg_price_at_leader: float        # leader's avg entry price
    avg_price_at_follower: float      # follower's avg entry price (price moved?)
    avg_price_move: float             # |follower_price - leader_price| mean
    weight: float                     # composite strength (0–1)


@dataclass
class WalletInfluenceGraph:
    nodes: list[str]                  # wallet IDs
    edges: list[InfluenceEdge]
    leaders: list[str]                # wallets with no strong predecessors + positive impact
    followers: list[str]
    influence_scores: dict[str, float]  # per wallet: how much others follow them
    computed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════════════
# 1. COPYABLE ALPHA SCORE
# ══════════════════════════════════════════════════════════════════════════

async def compute_copyable_alpha(
    db: AsyncSession,
    wallet_id,
    all_wallet_txs: list[WalletTransaction] | None = None,
) -> CopyableAlphaResult:
    """Full copyable alpha computation for one wallet."""

    result = await db.execute(
        select(WalletTransaction)
        .where(WalletTransaction.wallet_id == wallet_id)
        .order_by(WalletTransaction.occurred_at.asc())
    )
    txs = result.scalars().all()

    wid = str(wallet_id)

    if len(txs) < 3:
        return _empty_alpha_result(wid)

    prices     = [float(t.price)    for t in txs if t.price    is not None]
    sizes      = [float(t.size)     for t in txs if t.size     is not None]
    notionals  = [float(t.notional) for t in txs if t.notional is not None]
    lags       = [t.detection_lag_ms for t in txs if t.detection_lag_ms is not None]

    # ── 1. Timing score ────────────────────────────────────────────────
    timing_score, timing_detail = _compute_timing_score(txs, db)

    # ── 2. Persistence score ───────────────────────────────────────────
    persistence_score = _compute_persistence(txs, prices, notionals)

    # ── 3. Volatility-adjusted ROI ─────────────────────────────────────
    vol_roi = _compute_vol_adjusted_roi(txs, prices, notionals)

    # ── 4. Latency survivability ───────────────────────────────────────
    lat_surv = _compute_latency_survivability(prices, lags)

    # ── 5. Drawdown stability ──────────────────────────────────────────
    dd_stab = _compute_drawdown_stability(txs, prices, notionals)

    # ── 6. Information impact (price move after trade) ─────────────────
    info_impact, influence_detail = _compute_information_impact(txs, prices)

    # ── Suspiciousness penalty ─────────────────────────────────────────
    from app.intelligence.wallet_scorer import _suspiciousness, _estimate_profits
    profits = _estimate_profits(txs)
    susp_penalty = _suspiciousness(txs, profits) * 0.15   # max -0.15

    # ── Composite ─────────────────────────────────────────────────────
    raw = (
        _W_TIMING      * timing_score
        + _W_PERSISTENCE * persistence_score
        + _W_VOL_ROI     * vol_roi
        + _W_LATENCY_SURV * lat_surv
        + _W_DD_STABILITY * dd_stab
        + _W_INFO_IMPACT  * info_impact
        - susp_penalty
    )
    score = max(0.0, min(1.0, raw))

    # ── Alpha decay risk ───────────────────────────────────────────────
    decay_risk, decay_signal = _assess_decay_risk(
        persistence_score, lat_surv, timing_score, len(txs)
    )

    # ── Recommendation ─────────────────────────────────────────────────
    rec = "copy_now" if score >= 0.55 else "monitor" if score >= 0.30 else "avoid"

    return CopyableAlphaResult(
        wallet_id=wid,
        copyable_alpha_score=round(score, 4),
        timing_score=round(timing_score, 4),
        persistence_score=round(persistence_score, 4),
        vol_adjusted_roi=round(vol_roi, 4),
        latency_survivability=round(lat_surv, 4),
        drawdown_stability=round(dd_stab, 4),
        information_impact=round(info_impact, 4),
        suspiciousness_penalty=round(susp_penalty, 4),
        timing_detail=timing_detail,
        influence_detail=influence_detail,
        alpha_decay_risk=decay_risk,
        decay_signal=decay_signal,
        recommendation=rec,
    )


def _compute_timing_score(
    txs: list[WalletTransaction],
    db: AsyncSession,  # reserved for future snapshot lookups
) -> tuple[float, dict]:
    """
    Timing score: does the wallet trade BEFORE price moves?

    Proxy: buys at price p < 0.50 that later resolve profitably,
    or sells at p > 0.50. We measure price deviation from 0.5 at entry —
    good timing = buying cheap / selling expensive.

    Also checks: first-mover advantage (low detection lag relative to peers).
    """
    buys  = [t for t in txs if t.side == "BUY"  and t.price is not None]
    sells = [t for t in txs if t.side == "SELL" and t.price is not None]

    # Favourable entry ratio
    fav_buys  = [t for t in buys  if float(t.price) < 0.45]
    fav_sells = [t for t in sells if float(t.price) > 0.55]
    total = len(buys) + len(sells)
    fav_ratio = (len(fav_buys) + len(fav_sells)) / max(total, 1)

    # Detection lag advantage (lower is better)
    lags = [t.detection_lag_ms for t in txs if t.detection_lag_ms is not None]
    lag_score = 0.5
    if lags:
        median_lag = float(np.median(lags))
        # < 200ms = excellent; 200–800ms = ok; > 1500ms = poor
        lag_score = max(0.0, 1.0 - median_lag / 2000)

    timing = 0.6 * fav_ratio + 0.4 * lag_score

    detail = {
        "favourable_entry_ratio": round(fav_ratio, 4),
        "median_detection_lag_ms": float(np.median(lags)) if lags else None,
        "lag_score": round(lag_score, 4),
    }
    return min(timing, 1.0), detail


def _compute_persistence(
    txs: list[WalletTransaction],
    prices: list[float],
    notionals: list[float],
) -> float:
    """
    Persistence: is profitability consistent across time windows?
    Split trades into thirds; compute implied profitability for each window.
    Low variance across windows → high persistence.
    """
    if len(txs) < 6:
        return 0.4   # insufficient data

    chunk_size = len(txs) // 3
    chunks = [txs[i * chunk_size:(i + 1) * chunk_size] for i in range(3)]

    window_scores = []
    for chunk in chunks:
        ps = [float(t.price) for t in chunk if t.price]
        ns = [float(t.notional) for t in chunk if t.notional]
        if not ps:
            window_scores.append(0.5)
            continue
        # Proxy: fraction of trades at favourable prices
        fav = sum(1 for p in ps if abs(p - 0.5) > 0.05) / len(ps)
        window_scores.append(fav)

    if not window_scores:
        return 0.4

    mean_w = float(np.mean(window_scores))
    std_w  = float(np.std(window_scores))
    # High mean + low variance = persistent alpha
    persistence = mean_w * (1.0 - min(std_w * 2, 0.8))
    return max(0.0, min(1.0, persistence))


def _compute_vol_adjusted_roi(
    txs: list[WalletTransaction],
    prices: list[float],
    notionals: list[float],
) -> float:
    """
    Volatility-adjusted ROI: Sharpe-like metric.
    roi / std(per_trade_returns)
    Capped at 1.0 for scoring purposes.
    """
    if len(prices) < 3 or not notionals:
        return 0.3

    per_trade = [abs(p - 0.5) for p in prices]   # proxy: distance from fair coin
    if not per_trade:
        return 0.3

    mean_ret = float(np.mean(per_trade))
    std_ret  = float(np.std(per_trade)) + 1e-8

    sharpe_proxy = mean_ret / std_ret
    # Normalise: sharpe_proxy 0 → 0, 2+ → 1
    return min(sharpe_proxy / 2.0, 1.0)


def _compute_latency_survivability(
    prices: list[float],
    lags: list[int],
) -> float:
    """
    At typical copy latency (800ms), how much edge survives?

    Uses exponential decay model tuned to wallet's own median lag.
    Better wallets trade slower-moving markets or have longer-lived signals.
    """
    if not prices:
        return 0.3

    avg_edge = float(np.mean([abs(p - 0.5) for p in prices]))
    if avg_edge < 0.001:
        return 0.1

    median_lag = float(np.median(lags)) if lags else 800.0
    copy_lag_ms = 800  # system's realistic copy latency

    # Faster wallets → market follows them quickly → edge decays faster
    decay_half_life = max(200, 2000 - median_lag)   # ms
    decay_rate = math.log(2) / decay_half_life
    surviving_fraction = math.exp(-decay_rate * copy_lag_ms)

    return max(0.0, min(1.0, surviving_fraction))


def _compute_drawdown_stability(
    txs: list[WalletTransaction],
    prices: list[float],
    notionals: list[float],
) -> float:
    """
    Drawdown stability: 1 - (max_drawdown / expected_drawdown).
    Good wallets don't have catastrophic drawdowns relative to their typical swings.
    """
    if len(txs) < 4:
        return 0.5

    from app.intelligence.wallet_scorer import _estimate_profits, _max_drawdown
    profits = _estimate_profits(txs)
    dd = _max_drawdown(profits)

    # Normalise: 0% dd = 1.0, 50%+ dd = 0.0
    stability = max(0.0, 1.0 - dd * 2)
    return min(stability, 1.0)


def _compute_information_impact(
    txs: list[WalletTransaction],
    prices: list[float],
) -> tuple[float, dict]:
    """
    Information impact: does the wallet's price at trade time deviate
    significantly from 0.5? Large deviations suggest the wallet is acting
    on information (one side is clearly mispriced).

    Also measures "conviction size": larger notionals at extreme prices = strong signal.
    """
    if not prices:
        return 0.3, {}

    # Mean absolute price deviation from 0.5
    deviations = [abs(p - 0.5) for p in prices]
    mean_dev   = float(np.mean(deviations))

    # Conviction: fraction of trades with |price - 0.5| > 0.15 (clear opinion)
    conviction_trades = sum(1 for d in deviations if d > 0.15)
    conviction_ratio  = conviction_trades / len(deviations)

    # Combine: deviations proxy + conviction
    impact = 0.5 * min(mean_dev / 0.3, 1.0) + 0.5 * conviction_ratio

    detail = {
        "mean_price_deviation": round(mean_dev, 4),
        "conviction_trade_ratio": round(conviction_ratio, 4),
        "trade_count_used": len(prices),
    }
    return min(impact, 1.0), detail


def _assess_decay_risk(
    persistence: float,
    lat_surv: float,
    timing: float,
    trade_count: int,
) -> tuple[str, str]:
    """Classify how likely a wallet's alpha is to decay soon."""
    risk_score = 0.0

    if persistence < 0.35:
        risk_score += 0.4
    elif persistence < 0.5:
        risk_score += 0.2

    if lat_surv < 0.3:
        risk_score += 0.3

    if timing < 0.3:
        risk_score += 0.2

    if trade_count < 10:
        risk_score += 0.1

    if risk_score >= 0.7:
        level = "critical"
        msg = "Inconsistent alpha across windows + poor latency survivability. High chance edge is noise."
    elif risk_score >= 0.45:
        level = "high"
        msg = "Persistence or timing weakening. Monitor for 10+ more trades before increasing allocation."
    elif risk_score >= 0.25:
        level = "medium"
        msg = "Some variability detected. Keep watching but alpha still present."
    else:
        level = "low"
        msg = "Alpha appears stable across observed windows."

    return level, msg


def _empty_alpha_result(wid: str) -> CopyableAlphaResult:
    return CopyableAlphaResult(
        wallet_id=wid,
        copyable_alpha_score=0.0,
        timing_score=0.0,
        persistence_score=0.0,
        vol_adjusted_roi=0.0,
        latency_survivability=0.0,
        drawdown_stability=0.0,
        information_impact=0.0,
        suspiciousness_penalty=0.0,
        timing_detail={},
        influence_detail={},
        alpha_decay_risk="critical",
        decay_signal="Insufficient trade history (need >= 3 trades).",
        recommendation="avoid",
    )


# ══════════════════════════════════════════════════════════════════════════
# 2. WALLET INFLUENCE GRAPH
# ══════════════════════════════════════════════════════════════════════════

async def build_influence_graph(
    db: AsyncSession,
    max_lag_seconds: float = 600,   # trades within 10 min are "potentially related"
    min_observations: int = 2,
) -> WalletInfluenceGraph:
    """
    Detect information leaders vs followers.

    Algorithm:
    1. Group all trades by market_id.
    2. For each pair (wallet A, wallet B) in the same market:
       - Find instances where A traded before B (within max_lag_seconds).
       - Compute mean lag and outcome correlation.
    3. Wallets with many followers and no strong predecessors = leaders.
    4. Wallets that consistently follow others = followers.
    """

    result = await db.execute(
        select(WalletTransaction)
        .where(WalletTransaction.occurred_at.isnot(None))
        .order_by(
            WalletTransaction.market_id,
            WalletTransaction.occurred_at.asc(),
        )
    )
    all_txs = result.scalars().all()

    if not all_txs:
        return WalletInfluenceGraph(nodes=[], edges=[], leaders=[], followers=[], influence_scores={})

    # Group by market
    by_market: dict[str, list[WalletTransaction]] = defaultdict(list)
    for tx in all_txs:
        if tx.market_id:
            by_market[str(tx.market_id)].append(tx)

    # Build raw edge observations: (leader, follower, market, lag_s, same_side, leader_price, follower_price)
    edge_obs: dict[tuple[str, str, str], list[tuple[float, bool, float | None, float | None]]] = defaultdict(list)

    for market_id, market_txs in by_market.items():
        # Only process markets with trades from 2+ wallets
        wallets_in_market = list({str(t.wallet_id) for t in market_txs})
        if len(wallets_in_market) < 2:
            continue

        for i, tx_a in enumerate(market_txs):
            for tx_b in market_txs[i + 1:]:
                if str(tx_a.wallet_id) == str(tx_b.wallet_id):
                    continue

                if tx_b.occurred_at is None or tx_a.occurred_at is None:
                    continue

                lag = (tx_b.occurred_at - tx_a.occurred_at).total_seconds()
                if lag < 0 or lag > max_lag_seconds:
                    continue  # not in window

                same_side   = (tx_a.side == tx_b.side)
                leader_px   = float(tx_a.price)  if tx_a.price  is not None else None
                follower_px = float(tx_b.price)  if tx_b.price  is not None else None
                key = (str(tx_a.wallet_id), str(tx_b.wallet_id), market_id)
                edge_obs[key].append((lag, same_side, leader_px, follower_px))

    # Aggregate edges
    edges: list[InfluenceEdge] = []
    for (leader_id, follower_id, market_id), obs in edge_obs.items():
        if len(obs) < min_observations:
            continue

        lags_list    = [o[0] for o in obs]
        agree        = sum(1 for o in obs if o[1]) / len(obs)
        leader_px    = [o[2] for o in obs if o[2] is not None]
        follower_px  = [o[3] for o in obs if o[3] is not None]

        mean_lag = float(np.mean(lags_list))
        min_lag  = float(np.min(lags_list))
        max_lag  = float(np.max(lags_list))

        avg_lpx  = float(np.mean(leader_px))   if leader_px   else 0.5
        avg_fpx  = float(np.mean(follower_px)) if follower_px else 0.5
        # Price moved toward the follower → leader predicted correctly
        avg_move = float(np.mean([abs(f - l) for f, l in zip(follower_px, leader_px)])) if (leader_px and follower_px) else 0.0

        # Weight = agreement * inverse lag * price movement signal
        lag_norm  = max(0.0, 1.0 - mean_lag / max_lag_seconds)
        move_norm = min(avg_move / 0.05, 1.0)   # 5% move = full weight
        weight    = 0.4 * agree + 0.35 * lag_norm + 0.25 * move_norm

        edges.append(InfluenceEdge(
            leader_wallet_id=leader_id,
            follower_wallet_id=follower_id,
            market_id=market_id,
            mean_lag_seconds=round(mean_lag, 1),
            min_lag_seconds=round(min_lag, 1),
            max_lag_seconds=round(max_lag, 1),
            trade_count=len(obs),
            outcome_correlation=round(agree, 3),
            avg_price_at_leader=round(avg_lpx, 4),
            avg_price_at_follower=round(avg_fpx, 4),
            avg_price_move=round(avg_move, 4),
            weight=round(weight, 3),
        ))

    # Influence score per wallet: sum of outgoing edge weights
    influence_scores: dict[str, float] = defaultdict(float)
    follower_scores: dict[str, float] = defaultdict(float)
    for e in edges:
        influence_scores[e.leader_wallet_id]  += e.weight
        follower_scores[e.follower_wallet_id] += e.weight

    all_nodes = list({e.leader_wallet_id for e in edges} | {e.follower_wallet_id for e in edges})

    # Leaders: high outgoing influence, low incoming
    leaders = [
        w for w in all_nodes
        if influence_scores.get(w, 0) > 0.5
        and follower_scores.get(w, 0) < influence_scores.get(w, 0) * 0.5
    ]
    followers = [
        w for w in all_nodes
        if follower_scores.get(w, 0) > 0.5
        and w not in leaders
    ]

    return WalletInfluenceGraph(
        nodes=all_nodes,
        edges=edges,
        leaders=leaders,
        followers=followers,
        influence_scores=dict(influence_scores),
    )


# ══════════════════════════════════════════════════════════════════════════
# 3. ALPHA DECAY MONITORING (real-time alert)
# ══════════════════════════════════════════════════════════════════════════

async def detect_alpha_decay(
    db: AsyncSession,
    wallet_id,
    rolling_windows: list[int] = (10, 20, 40),
) -> dict:
    """
    Compare wallet performance across rolling windows of different sizes.

    If recent window (10 trades) PF < older window (40 trades) PF → decaying.
    Returns decay_alert (bool) + quantified decay magnitude.
    """
    result = await db.execute(
        select(WalletTransaction)
        .where(WalletTransaction.wallet_id == wallet_id)
        .order_by(WalletTransaction.occurred_at.desc())
        .limit(max(rolling_windows))
    )
    txs_desc = list(result.scalars().all())

    if len(txs_desc) < 10:
        return {"decay_alert": False, "reason": "insufficient_data", "windows": {}}

    from app.intelligence.wallet_scorer import _estimate_profits

    window_metrics: dict[str, dict] = {}
    for w_size in rolling_windows:
        chunk = txs_desc[:w_size]
        profits = _estimate_profits(chunk)
        wins   = [p for p in profits if p > 0]
        losses = [p for p in profits if p <= 0]
        gp = sum(wins)
        gl = abs(sum(losses))
        pf = gp / gl if gl > 0 else (float("inf") if gp > 0 else None)
        wr = len(wins) / len(profits) if profits else 0
        window_metrics[f"last_{w_size}"] = {
            "trade_count": len(chunk),
            "profit_factor": round(pf, 3) if pf not in (None, float("inf")) else pf,
            "win_rate": round(wr, 3),
        }

    # Compare newest vs oldest window
    new_pf = window_metrics[f"last_{rolling_windows[0]}"].get("profit_factor")
    old_pf = window_metrics[f"last_{rolling_windows[-1]}"].get("profit_factor")

    decay_alert = False
    decay_magnitude = None
    decay_reason = "stable"

    if new_pf is not None and old_pf is not None and old_pf not in (float("inf"),):
        if isinstance(new_pf, float) and isinstance(old_pf, float):
            decay_magnitude = old_pf - new_pf
            if decay_magnitude > 0.5:
                decay_alert = True
                decay_reason = (
                    f"Recent PF ({new_pf:.2f}) significantly below long-term PF ({old_pf:.2f}). "
                    f"Alpha decaying by {decay_magnitude:.2f} PF units."
                )
        elif new_pf is None and old_pf is not None:
            decay_alert = True
            decay_reason = "Recent window has no profitable trades despite historical edge."

    return {
        "wallet_id": str(wallet_id),
        "decay_alert": decay_alert,
        "decay_magnitude": round(decay_magnitude, 3) if decay_magnitude else None,
        "reason": decay_reason,
        "windows": window_metrics,
    }
