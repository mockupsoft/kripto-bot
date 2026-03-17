"""Leader Copy Strategy — copies only wallets identified as information leaders.

Information Propagation Model (v2):
  prop_signal = leader_score × size_factor × liquidity_factor × time_decay
  Entry window: 5–45 seconds after leader trade (tighter = better)

Entry criteria (layered):
  1. Wallet is in the influence graph's leader set
  2. copyable_alpha_score > min_alpha (default 0.35)
  3. alpha_decay_risk not critical
  4. prop_signal > MIN_PROP_SIGNAL (information propagation gate)
  5. Standard signal filter (net_edge > 0 after costs)
  6. Not exceeding exposure limits
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.paper_executor import execute_paper_trade
from app.models.paper import PaperPosition
from app.models.signal import TradeSignal, SignalDecision
from app.signals.signal_filter import SignalFilter
from app.strategies.base import BaseStrategy
from app.risk.position_sizer import compute_position_size

_MIN_ALPHA_SCORE    = float(os.getenv("LEADER_COPY_MIN_ALPHA",      "0.35"))
_MIN_INFLUENCE      = float(os.getenv("LEADER_COPY_MIN_INFLUENCE",  "0.4"))
_MAX_LAG_S          = float(os.getenv("LEADER_COPY_MAX_LAG_S",      "300"))
_GRAPH_TTL_MINUTES  = int(os.getenv("LEADER_COPY_GRAPH_TTL_MIN",    "60"))
_MIN_PROP_SIGNAL    = float(os.getenv("LEADER_MIN_PROP_SIGNAL",     "0.05"))

_cached_graph = None
_cache_time: datetime | None = None


async def _get_influence_graph(db: AsyncSession):
    global _cached_graph, _cache_time
    now = datetime.now(timezone.utc)
    if (
        _cached_graph is None
        or _cache_time is None
        or (now - _cache_time).total_seconds() > _GRAPH_TTL_MINUTES * 60
    ):
        from app.intelligence.wallet_alpha import build_influence_graph
        _cached_graph = await build_influence_graph(db, max_lag_seconds=_MAX_LAG_S)
        _cache_time = now
    return _cached_graph


class LeaderCopyStrategy(BaseStrategy):
    """Copy only wallets that are information leaders in the influence graph."""

    name = "leader_copy"

    def __init__(
        self,
        min_alpha_score: float = _MIN_ALPHA_SCORE,
        min_influence_score: float = _MIN_INFLUENCE,
    ):
        self.min_alpha_score     = min_alpha_score
        self.min_influence_score = min_influence_score
        self._filter = SignalFilter(
            min_net_edge=0.01,
            max_spread=0.08,
            max_signal_age_ms=5000,
            min_liquidity_usd=30.0,
        )

    async def evaluate(
        self,
        db: AsyncSession,
        signal: TradeSignal,
        current_price: float,
        current_spread: float,
        available_depth: float,
        book_levels=None,
        bankroll: float = 900.0,
        exposure_pct: float = 0.0,
        **kwargs: object,
    ) -> SignalDecision | None:
        if bankroll <= 0:
            return None

        # ── Gate 1: Wallet must be a graph leader ─────────────────────
        graph = await _get_influence_graph(db)
        wallet_id = str(signal.source_wallet_id) if signal.source_wallet_id else None
        if not wallet_id:
            return None

        influence_score = graph.influence_scores.get(wallet_id, 0.0)
        is_leader = wallet_id in graph.leaders

        if not is_leader and influence_score < self.min_influence_score:
            return None

        # ── Gate 2: copyable_alpha_score ─────────────────────────────
        from app.intelligence.wallet_alpha import compute_copyable_alpha
        alpha = await compute_copyable_alpha(db, signal.source_wallet_id)

        if alpha.copyable_alpha_score < self.min_alpha_score:
            return None

        if alpha.alpha_decay_risk == "critical":
            return None

        # ── Gate 3: Information Propagation Signal ────────────────────
        from app.intelligence.leader_impact import compute_prop_signal
        from app.models.wallet import WalletScore

        ws_result = await db.execute(
            select(WalletScore.composite_score)
            .where(WalletScore.wallet_id == signal.source_wallet_id)
            .order_by(WalletScore.scored_at.desc())
            .limit(1)
        )
        wallet_score = float(ws_result.scalar() or 0.5)

        prop = await compute_prop_signal(
            db=db,
            wallet_id=wallet_id,
            market_id=str(signal.market_id),
            side=signal.side or "BUY",
            notional=10.0,          # default notional if not on signal
            trade_time=signal.created_at,
            wallet_score=wallet_score,
            available_depth_usd=available_depth,
        )

        if prop.prop_signal < _MIN_PROP_SIGNAL:
            return None

        if not prop.entry_still_valid:
            return None

        # ── Gate 4: standard signal filter ───────────────────────────
        filter_result = await self._filter.evaluate(
            db=db,
            signal=signal,
            current_price=current_price,
            current_spread=current_spread,
            available_depth_usd=available_depth,
            current_bankroll=bankroll,
            current_exposure_pct=exposure_pct,
            snapshot_cache=kwargs.get("snapshot_cache"),
            position_counts=kwargs.get("position_counts"),
        )
        if filter_result.decision != "accept":
            return None

        # ── Size: Kelly × prop_signal boost ──────────────────────────
        conf_factor = float((signal.metadata_ or {}).get("confidence_factor", 1.0))
        prop_boost  = min(1.0, conf_factor * (1.0 + prop.prop_signal * 0.2))

        sizing = compute_position_size(
            model_probability=float(signal.model_probability or 0.55),
            model_confidence=float(signal.model_confidence or 0.5),
            market_price=current_price,
            available_bankroll=bankroll,
            kelly_fraction=0.25,
            max_position_pct=0.08,
            confidence_factor=prop_boost,
            side=signal.side or "BUY",
        )

        if sizing.proposed_size_usd <= 0:
            return None

        decision = await self._filter.record_decision(
            db=db,
            signal=signal,
            result=filter_result,
            kelly_fraction=sizing.capped_fraction,
            proposed_size=sizing.proposed_size_usd,
            available_bankroll=bankroll,
            current_exposure_pct=exposure_pct,
        )
        return decision

    async def execute(
        self,
        db: AsyncSession,
        signal: TradeSignal,
        decision: SignalDecision,
        book_levels: dict | None,
        book_snapshot_id: int | None,
    ) -> PaperPosition | None:
        if decision.decision != "accept":
            return None

        return await execute_paper_trade(
            db=db,
            signal_id=signal.id,
            decision_id=decision.id,
            market_id=signal.market_id,
            side=signal.side or "BUY",
            outcome="Yes",
            requested_size=float(decision.proposed_size or 0),
            market_price=float(signal.market_price or 0.5),
            strategy=self.name,
            source_wallet_id=signal.source_wallet_id,
            book_levels=book_levels,
            book_snapshot_id=book_snapshot_id,
            fees_enabled=True,
        )
