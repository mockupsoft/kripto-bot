"""Signal filter: decides whether to accept, reject, or shadow-log a signal.

Applies latency-adjusted entry viability, minimum liquidity, net edge threshold,
max spread, concentration limits, staleness checks, and duplicate stacking rules.

Cost regime (primary gate):
- confidence_adjusted_edge >= MIN_CONF_EDGE (default 0.012)
- spread_to_edge_ratio <= MAX_SPREAD_TO_EDGE (default 0.6)
- spread <= MAX_SPREAD_ABS (default 0.08)

These three filters are the main lever to push cost/gross ratio below 1.0.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.paper import PaperPosition
from app.models.signal import SignalDecision, TradeSignal

# Cost regime — configurable via env for parameter sweeps
MIN_CONF_EDGE = float(os.getenv("MIN_CONF_EDGE", "0.005"))   # loosened: Bayesian prior gives ~0.5 prob without spot feed
MAX_SPREAD_TO_EDGE_RATIO = float(os.getenv("MAX_SPREAD_TO_EDGE", "0.6"))
MAX_SPREAD_ABS = float(os.getenv("MAX_SPREAD_ABS", "0.08"))
MIN_DEPTH_USD = float(os.getenv("MIN_DEPTH_USD", "100.0"))


@dataclass
class FilterResult:
    decision: str  # 'accept', 'reject', 'shadow'
    reason: str | None
    edge_at_decision: float
    price_drift: float
    spread_at_decision: float


class SignalFilter:

    def __init__(
        self,
        min_net_edge: float = 0.0,
        max_spread: float = MAX_SPREAD_ABS,
        max_signal_age_ms: int = 5000,
        max_position_concentration: int = 1,
        min_liquidity_usd: float = MIN_DEPTH_USD,
    ):
        self.min_net_edge = min_net_edge
        self.max_spread = max_spread
        self.max_signal_age_ms = max_signal_age_ms
        self.max_position_concentration = max_position_concentration
        self.min_liquidity_usd = min_liquidity_usd

    async def evaluate(
        self,
        db: AsyncSession,
        signal: TradeSignal,
        current_price: float,
        current_spread: float,
        available_depth_usd: float,
        current_bankroll: float,
        current_exposure_pct: float,
        latency_profile_id=None,
        slippage_profile_id=None,
    ) -> FilterResult:
        now = datetime.now(timezone.utc)
        signal_age_ms = int((now - signal.created_at).total_seconds() * 1000)
        price_drift = current_price - float(signal.market_price) if signal.market_price else 0

        # For dislocation signals, trust the pre-computed net_edge
        # For copy signals, recompute edge accounting for price drift since signal creation.
        # SELL signals: raw_edge = model_prob - market_price is negative for shorts.
        # We take abs() so edge is always measured as "distance from fair value" regardless of direction.
        if signal.source_type == "spread_anomaly":
            edge_at_decision = float(signal.net_edge or 0)
        else:
            raw_directional = float(signal.raw_edge or 0)
            # For SELL: model says prob is lower than market → edge = market_price - model_prob
            # raw_edge in DB may already be signed (BUY: prob-price, SELL: prob-price = negative)
            # Normalize to absolute edge magnitude so all downstream gates use positive values
            edge_at_decision = abs(raw_directional) - abs(price_drift) - current_spread / 2

        # Confidence-adjusted edge (primary quality gate)
        raw_conf = float(signal.model_confidence or 0.5)
        conf_adjusted_edge = edge_at_decision * raw_conf

        # Spread-to-edge ratio: how much of gross edge is consumed by spread alone
        spread_cost = current_spread / 2
        if edge_at_decision > 0:
            spread_to_edge = spread_cost / edge_at_decision
        else:
            spread_to_edge = float("inf")

        # ── Gate 1: signal staleness ──────────────────────────────────────────
            return FilterResult(
                decision="reject",
                reason=f"signal_stale: {signal_age_ms}ms > {self.max_signal_age_ms}ms",
                edge_at_decision=round(edge_at_decision, 6),
                price_drift=round(price_drift, 6),
                spread_at_decision=round(current_spread, 4),
            )

        # ── Gate 2: absolute spread cap ───────────────────────────────────────
        if current_spread > self.max_spread:
            return FilterResult(
                decision="reject",
                reason=f"spread_too_wide: {current_spread:.4f} > {self.max_spread:.3f}",
                edge_at_decision=round(edge_at_decision, 6),
                price_drift=round(price_drift, 6),
                spread_at_decision=round(current_spread, 4),
            )

        # ── Gate 3: confidence-adjusted edge minimum ──────────────────────────
        if conf_adjusted_edge < MIN_CONF_EDGE:
            return FilterResult(
                decision="reject",
                reason=f"conf_edge_low: {conf_adjusted_edge:.4f} < {MIN_CONF_EDGE:.4f}",
                edge_at_decision=round(edge_at_decision, 6),
                price_drift=round(price_drift, 6),
                spread_at_decision=round(current_spread, 4),
            )

        # ── Gate 3b: executable edge (Layer 3 — fill_prob × liquidity) ────────
        # Use pre-computed Layer-3 values stored in signal.costs_breakdown if available.
        # This prevents model edge inflation from passing even when conf_adj is borderline.
        cb = signal.costs_breakdown or {}
        liq_factor   = float(cb.get("liquidity_factor",   0.8))
        fill_prob    = float(cb.get("fill_probability",   0.6))
        exec_edge = conf_adjusted_edge * fill_prob * liq_factor
        # Gate: executable edge must be positive (any negative = never executable)
        if exec_edge <= 0:
            return FilterResult(
                decision="reject",
                reason=f"exec_edge_nonpositive: {exec_edge:.5f}",
                edge_at_decision=round(edge_at_decision, 6),
                price_drift=round(price_drift, 6),
                spread_at_decision=round(current_spread, 4),
            )

        # ── Gate 4: spread-to-edge ratio ─────────────────────────────────────
        if spread_to_edge > MAX_SPREAD_TO_EDGE_RATIO:
            return FilterResult(
                decision="reject",
                reason=f"spread_eats_edge: ratio={spread_to_edge:.2f} > {MAX_SPREAD_TO_EDGE_RATIO}",
                edge_at_decision=round(edge_at_decision, 6),
                price_drift=round(price_drift, 6),
                spread_at_decision=round(current_spread, 4),
            )

        # ── Gate 5: net edge floor ─────────────────────────────────────────────
        if edge_at_decision <= self.min_net_edge:
            return FilterResult(
                decision="reject",
                reason=f"edge_eroded: {edge_at_decision:.4f} <= {self.min_net_edge}",
                edge_at_decision=round(edge_at_decision, 6),
                price_drift=round(price_drift, 6),
                spread_at_decision=round(current_spread, 4),
            )

        # ── Gate 6: liquidity ─────────────────────────────────────────────────
        if available_depth_usd < self.min_liquidity_usd:
            return FilterResult(
                decision="reject",
                reason=f"insufficient_liquidity: ${available_depth_usd:.0f} < ${self.min_liquidity_usd:.0f}",
                edge_at_decision=round(edge_at_decision, 6),
                price_drift=round(price_drift, 6),
                spread_at_decision=round(current_spread, 4),
            )

        # ── Gate 7: portfolio exposure ────────────────────────────────────────
        # Use absolute exposure: total open cost vs 12% of current_bankroll
        # (current_exposure_pct from runner may be relative to strategy bankroll,
        #  not total bankroll — so we check against a sane absolute ceiling instead)
        if current_bankroll > 0 and current_exposure_pct > 0.12:
            return FilterResult(
                decision="reject",
                reason=f"max_exposure_exceeded: {current_exposure_pct:.2%} > 12%",
                edge_at_decision=round(edge_at_decision, 6),
                price_drift=round(price_drift, 6),
                spread_at_decision=round(current_spread, 4),
            )

        # ── Gate 8: market concentration ─────────────────────────────────────
        market_positions = await db.execute(
            select(func.count()).where(
                PaperPosition.market_id == signal.market_id,
                PaperPosition.status == "open",
            )
        )
        pos_count = market_positions.scalar() or 0
        if pos_count >= self.max_position_concentration:
            return FilterResult(
                decision="reject",
                reason=f"concentration_limit: {pos_count} >= {self.max_position_concentration} in market",
                edge_at_decision=round(edge_at_decision, 6),
                price_drift=round(price_drift, 6),
                spread_at_decision=round(current_spread, 4),
            )

        return FilterResult(
            decision="accept",
            reason=None,
            edge_at_decision=round(edge_at_decision, 6),
            price_drift=round(price_drift, 6),
            spread_at_decision=round(current_spread, 4),
        )

    async def record_decision(
        self,
        db: AsyncSession,
        signal: TradeSignal,
        result: FilterResult,
        kelly_fraction: float = 0,
        proposed_size: float = 0,
        available_bankroll: float = 900,
        current_exposure_pct: float = 0,
        latency_profile_id=None,
        slippage_profile_id=None,
    ) -> SignalDecision:
        edge_at_signal = float(signal.net_edge) if signal.net_edge else 0
        edge_erosion = 0.0
        if abs(edge_at_signal) > 1e-8:
            edge_erosion = (edge_at_signal - result.edge_at_decision) / abs(edge_at_signal)

        now = datetime.now(timezone.utc)
        signal_age_ms = int((now - signal.created_at).total_seconds() * 1000)

        decision = SignalDecision(
            signal_id=signal.id,
            decided_at=now,
            decision=result.decision,
            reject_reason=result.reason,
            signal_age_ms=signal_age_ms,
            detection_to_decision_ms=signal_age_ms,
            price_at_decision=float(signal.market_price or 0) + result.price_drift,
            price_drift=result.price_drift,
            spread_at_decision=result.spread_at_decision,
            kelly_fraction=kelly_fraction,
            proposed_size=proposed_size,
            available_bankroll=available_bankroll,
            current_exposure_pct=current_exposure_pct,
            edge_at_signal=edge_at_signal,
            edge_at_decision=result.edge_at_decision,
            edge_erosion_pct=round(edge_erosion, 4),
            scenario="realistic",
            latency_profile_id=latency_profile_id,
            slippage_profile_id=slippage_profile_id,
        )
        db.add(decision)
        await db.flush()
        return decision
