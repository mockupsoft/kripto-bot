"""Leader Impact Measurement — Information Propagation Engine.

Core thesis: In prediction markets, some traders use information before others.
After a leader trades, price moves ~30-120 seconds later as followers react.
This module measures that delay and impact to power the propagation strategy.

Key outputs per wallet:
  avg_aligned_move_60s  — direction-adjusted price move 60s after their trade
  hit_rate_60s          — % of trades where price moved in their direction
  avg_impact_notional   — impact per unit of notional traded
  prop_signal           — composite propagation signal (0–1)
  entry_window_seconds  — optimal entry window before follower wave
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ── Config ────────────────────────────────────────────────────────────────────
_MIN_SCORE      = float(os.getenv("LEADER_MIN_SCORE",       "0.50"))
_LOOKBACK_HOURS = int(os.getenv("LEADER_IMPACT_LOOKBACK_H", "72"))
_MIN_TRADES     = int(os.getenv("LEADER_IMPACT_MIN_TRADES", "5"))
_ENTRY_WINDOW_S = int(os.getenv("LEADER_ENTRY_WINDOW_S",    "45"))

# Price move windows in seconds
_WINDOWS = [30, 60, 120, 300]


@dataclass
class LeaderImpact:
    wallet_id: str
    n_trades: int                         # total trades analysed
    n_with_price_data: int                # trades where we had snapshot data

    # Direction-adjusted price moves (positive = leader was right)
    avg_aligned_move_30s:  float | None
    avg_aligned_move_60s:  float | None
    avg_aligned_move_120s: float | None
    avg_aligned_move_300s: float | None

    # Hit rates (% of trades price moved in leader's direction)
    hit_rate_30s:  float | None
    hit_rate_60s:  float | None
    hit_rate_120s: float | None

    # Best window (seconds) — when impact is strongest
    best_window_s: int

    # Information propagation score (0–1)
    prop_signal: float

    # Recommended entry window (how many seconds after detecting their trade)
    entry_window_s: int

    computed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class PropSignal:
    """Real-time propagation signal for a single trade event."""
    wallet_id: str
    market_id: str
    side: str
    notional: float
    trade_time: datetime

    leader_score: float        # composite wallet score
    prop_impact_score: float   # historical impact score of this wallet
    size_factor: float         # normalized trade size (bigger = stronger signal)
    liquidity_factor: float    # available market depth factor
    time_decay: float          # how much signal has decayed since trade

    prop_signal: float         # final: leader × size × liquidity × decay
    entry_still_valid: bool    # True if within entry window


def _time_decay(seconds_since_trade: float, window_s: float = _ENTRY_WINDOW_S) -> float:
    """Exponential decay: 1.0 at t=0, ~0.37 at t=window_s, 0 at t=3*window_s."""
    if seconds_since_trade <= 0:
        return 1.0
    if seconds_since_trade >= window_s * 3:
        return 0.0
    import math
    return math.exp(-seconds_since_trade / window_s)


async def measure_leader_impact(
    db: AsyncSession,
    wallet_id: UUID,
    lookback_hours: int = _LOOKBACK_HOURS,
    min_trades: int = _MIN_TRADES,
) -> LeaderImpact | None:
    """Measure how much price moves after this wallet trades.

    Uses historical wallet_transactions + market_snapshots to compute
    direction-adjusted price moves at 30s, 60s, 120s, 300s windows.
    """
    sql = text("""
        WITH trades AS (
          SELECT wt.id, wt.market_id, wt.occurred_at, wt.side, wt.notional
          FROM wallet_transactions wt
          WHERE wt.wallet_id = :wallet_id
            AND wt.occurred_at > NOW() - INTERVAL '1 hour' * :lookback_h
          ORDER BY wt.occurred_at DESC
          LIMIT 500
        ),
        price_moves AS (
          SELECT
            t.id,
            t.side,
            t.notional,
            snap_base.midpoint                                  AS p0,
            snap_30.midpoint                                    AS p30,
            snap_60.midpoint                                    AS p60,
            snap_120.midpoint                                   AS p120,
            snap_300.midpoint                                   AS p300
          FROM trades t
          LEFT JOIN LATERAL (
            SELECT midpoint FROM market_snapshots
            WHERE market_id = t.market_id
              AND captured_at <= t.occurred_at
            ORDER BY captured_at DESC LIMIT 1
          ) snap_base ON true
          LEFT JOIN LATERAL (
            SELECT midpoint FROM market_snapshots
            WHERE market_id = t.market_id
              AND captured_at BETWEEN t.occurred_at + INTERVAL '15 seconds'
                                  AND t.occurred_at + INTERVAL '45 seconds'
            ORDER BY captured_at LIMIT 1
          ) snap_30 ON true
          LEFT JOIN LATERAL (
            SELECT midpoint FROM market_snapshots
            WHERE market_id = t.market_id
              AND captured_at BETWEEN t.occurred_at + INTERVAL '45 seconds'
                                  AND t.occurred_at + INTERVAL '90 seconds'
            ORDER BY captured_at LIMIT 1
          ) snap_60 ON true
          LEFT JOIN LATERAL (
            SELECT midpoint FROM market_snapshots
            WHERE market_id = t.market_id
              AND captured_at BETWEEN t.occurred_at + INTERVAL '90 seconds'
                                  AND t.occurred_at + INTERVAL '150 seconds'
            ORDER BY captured_at LIMIT 1
          ) snap_120 ON true
          LEFT JOIN LATERAL (
            SELECT midpoint FROM market_snapshots
            WHERE market_id = t.market_id
              AND captured_at BETWEEN t.occurred_at + INTERVAL '240 seconds'
                                  AND t.occurred_at + INTERVAL '360 seconds'
            ORDER BY captured_at LIMIT 1
          ) snap_300 ON true
          WHERE snap_base.midpoint IS NOT NULL
        )
        SELECT
          COUNT(*)                                                          AS n_trades,
          COUNT(p60)                                                        AS n_with_60s,
          -- direction-adjusted means: positive = leader was correct
          AVG(CASE WHEN side='BUY' AND p30  IS NOT NULL THEN p30  - p0
                   WHEN side='SELL' AND p30 IS NOT NULL THEN p0 - p30  END) AS move_30s,
          AVG(CASE WHEN side='BUY' AND p60  IS NOT NULL THEN p60  - p0
                   WHEN side='SELL' AND p60 IS NOT NULL THEN p0 - p60  END) AS move_60s,
          AVG(CASE WHEN side='BUY' AND p120 IS NOT NULL THEN p120 - p0
                   WHEN side='SELL' AND p120 IS NOT NULL THEN p0 - p120 END) AS move_120s,
          AVG(CASE WHEN side='BUY' AND p300 IS NOT NULL THEN p300 - p0
                   WHEN side='SELL' AND p300 IS NOT NULL THEN p0 - p300 END) AS move_300s,
          -- hit rates
          AVG(CASE WHEN (side='BUY' AND p30  > p0) OR (side='SELL' AND p30  < p0) THEN 1.0
                   WHEN p30  IS NOT NULL THEN 0.0 END)                    AS hit_30s,
          AVG(CASE WHEN (side='BUY' AND p60  > p0) OR (side='SELL' AND p60  < p0) THEN 1.0
                   WHEN p60  IS NOT NULL THEN 0.0 END)                    AS hit_60s,
          AVG(CASE WHEN (side='BUY' AND p120 > p0) OR (side='SELL' AND p120 < p0) THEN 1.0
                   WHEN p120 IS NOT NULL THEN 0.0 END)                    AS hit_120s
        FROM price_moves
    """)

    row = (await db.execute(sql, {"wallet_id": str(wallet_id), "lookback_h": lookback_hours})).mappings().one_or_none()
    if not row:
        return None

    n_trades = int(row["n_trades"] or 0)
    n_data   = int(row["n_with_60s"] or 0)

    if n_trades < min_trades or n_data == 0:
        return None

    def _f(v) -> float | None:
        return round(float(v), 6) if v is not None else None

    move_30s  = _f(row["move_30s"])
    move_60s  = _f(row["move_60s"])
    move_120s = _f(row["move_120s"])
    move_300s = _f(row["move_300s"])
    hit_60s   = _f(row["hit_60s"])

    # Best window: whichever positive move is largest
    window_moves = {30: move_30s, 60: move_60s, 120: move_120s, 300: move_300s}
    valid_windows = {w: m for w, m in window_moves.items() if m is not None and m > 0}
    best_window = max(valid_windows, key=valid_windows.get) if valid_windows else 60

    # prop_signal: combines move magnitude and directional consistency
    # Scale: 0.05 aligned move + 0.6 hit rate = ~0.5 signal
    move_component = max(0.0, min(1.0, (move_60s or 0) / 0.05))   # normalized at 5% move
    hit_component  = max(0.0, (hit_60s or 0.5) - 0.5) * 2        # 0 at 50%, 1 at 100%
    prop_signal    = round((move_component * 0.5 + hit_component * 0.5), 4)

    # Entry window: tighter if best impact is at 30s, wider if at 120s+
    entry_window = min(_ENTRY_WINDOW_S, best_window // 2 + 15)

    return LeaderImpact(
        wallet_id=str(wallet_id),
        n_trades=n_trades,
        n_with_price_data=n_data,
        avg_aligned_move_30s=move_30s,
        avg_aligned_move_60s=move_60s,
        avg_aligned_move_120s=move_120s,
        avg_aligned_move_300s=move_300s,
        hit_rate_30s=_f(row["hit_30s"]),
        hit_rate_60s=hit_60s,
        hit_rate_120s=_f(row["hit_120s"]),
        best_window_s=best_window,
        prop_signal=prop_signal,
        entry_window_s=entry_window,
    )


async def compute_prop_signal(
    db: AsyncSession,
    wallet_id: str,
    market_id: str,
    side: str,
    notional: float,
    trade_time: datetime,
    wallet_score: float,
    available_depth_usd: float = 200.0,
) -> PropSignal:
    """Compute real-time propagation signal for a detected leader trade.

    Called by leader_copy strategy when a leader wallet trade is detected.
    Returns a PropSignal with composite score and whether entry is still valid.
    """
    now = datetime.now(timezone.utc)
    seconds_elapsed = (now - trade_time).total_seconds()

    # Historical impact from DB
    impact = await measure_leader_impact(db, UUID(wallet_id))

    # prop_impact_score: use historical if available, else wallet_score as proxy
    prop_impact = impact.prop_signal if impact else max(0.0, (wallet_score - 0.4) * 1.5)
    entry_window = impact.entry_window_s if impact else _ENTRY_WINDOW_S

    # Size factor: larger trades = stronger signal (log-normalized)
    import math
    size_factor = min(1.0, math.log1p(notional / 10) / math.log1p(100))

    # Liquidity factor
    liquidity_factor = min(1.0, available_depth_usd / 500.0)
    liquidity_factor = max(0.3, liquidity_factor)

    # Time decay
    decay = _time_decay(seconds_elapsed, window_s=entry_window)

    # Final signal
    prop_score = prop_impact * size_factor * liquidity_factor * decay
    prop_score = round(min(1.0, prop_score), 4)

    return PropSignal(
        wallet_id=wallet_id,
        market_id=market_id,
        side=side,
        notional=notional,
        trade_time=trade_time,
        leader_score=wallet_score,
        prop_impact_score=prop_impact,
        size_factor=round(size_factor, 4),
        liquidity_factor=round(liquidity_factor, 4),
        time_decay=round(decay, 4),
        prop_signal=prop_score,
        entry_still_valid=decay > 0.1,   # > 10% of signal strength remains
    )


async def get_leader_impact_leaderboard(
    db: AsyncSession,
    min_score: float = _MIN_SCORE,
    top_n: int = 20,
) -> list[dict]:
    """Return top wallets ranked by their measured propagation impact.

    This is the core of the information propagation strategy:
    wallets where avg_aligned_move_60s > 0 consistently are true leaders.
    """
    sql = text("""
        WITH top_wallets AS (
          SELECT DISTINCT ON (ws.wallet_id) ws.wallet_id, ws.composite_score
          FROM wallet_scores ws
          WHERE ws.composite_score >= :min_score
          ORDER BY ws.wallet_id, ws.scored_at DESC
        ),
        trades AS (
          SELECT wt.wallet_id, wt.market_id, wt.occurred_at, wt.side, wt.notional
          FROM wallet_transactions wt
          JOIN top_wallets tw ON tw.wallet_id = wt.wallet_id
          WHERE wt.occurred_at > NOW() - INTERVAL '72 hours'
        ),
        impact AS (
          SELECT
            t.wallet_id,
            COUNT(*)                                        AS n_trades,
            COUNT(snap_60.midpoint)                        AS n_60s,
            AVG(CASE WHEN side='BUY' AND snap_60.midpoint IS NOT NULL THEN snap_60.midpoint - snap_base.midpoint
                     WHEN side='SELL' AND snap_60.midpoint IS NOT NULL THEN snap_base.midpoint - snap_60.midpoint END) AS move_60s,
            AVG(CASE WHEN (side='BUY' AND snap_60.midpoint > snap_base.midpoint)
                      OR  (side='SELL' AND snap_60.midpoint < snap_base.midpoint)
                     THEN 1.0 WHEN snap_60.midpoint IS NOT NULL THEN 0.0 END)  AS hit_60s
          FROM trades t
          LEFT JOIN LATERAL (
            SELECT midpoint FROM market_snapshots
            WHERE market_id = t.market_id AND captured_at <= t.occurred_at
            ORDER BY captured_at DESC LIMIT 1
          ) snap_base ON true
          LEFT JOIN LATERAL (
            SELECT midpoint FROM market_snapshots
            WHERE market_id = t.market_id
              AND captured_at BETWEEN t.occurred_at + INTERVAL '45s' AND t.occurred_at + INTERVAL '90s'
            ORDER BY captured_at LIMIT 1
          ) snap_60 ON true
          WHERE snap_base.midpoint IS NOT NULL
          GROUP BY t.wallet_id
          HAVING COUNT(*) >= :min_trades AND COUNT(snap_60.midpoint) >= 3
        )
        SELECT
          i.wallet_id,
          i.n_trades,
          i.n_60s,
          ROUND(i.move_60s::numeric, 5)  AS avg_move_60s,
          ROUND(i.hit_60s::numeric, 3)   AS hit_rate_60s,
          ROUND(tw.composite_score::numeric, 3) AS wallet_score,
          ROUND(
            LEAST(1.0, GREATEST(0.0, i.move_60s) / 0.05) * 0.5
            + GREATEST(0.0, i.hit_60s - 0.5) * 2 * 0.5,
            4
          ) AS prop_signal
        FROM impact i
        JOIN top_wallets tw ON tw.wallet_id = i.wallet_id
        WHERE i.move_60s IS NOT NULL
        ORDER BY prop_signal DESC
        LIMIT :top_n
    """)

    rows = (await db.execute(sql, {
        "min_score": min_score,
        "min_trades": _MIN_TRADES,
        "top_n": top_n,
    })).mappings().all()

    return [
        {
            "wallet_id": str(r["wallet_id"]),
            "n_trades": int(r["n_trades"]),
            "n_with_price_data": int(r["n_60s"]),
            "avg_aligned_move_60s": float(r["avg_move_60s"]) if r["avg_move_60s"] else None,
            "hit_rate_60s": float(r["hit_rate_60s"]) if r["hit_rate_60s"] else None,
            "wallet_score": float(r["wallet_score"]) if r["wallet_score"] else None,
            "prop_signal": float(r["prop_signal"]) if r["prop_signal"] else 0.0,
        }
        for r in rows
    ]
