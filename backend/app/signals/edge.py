"""Edge calculator: determines whether a detected dislocation has real positive EV.

3-layer edge model (real prediction market approach):
  Layer 1 — Fair Probability:   model_probability vs market_price  → raw_edge
  Layer 2 — Tradable Edge:      raw_edge − spread − fee − slippage → net_edge
  Layer 3 — Executable Edge:    net_edge × confidence × liquidity_factor → final edge

Edge inflation fix:
  - MAX_RAW_EDGE = 0.15: caps probability divergence (wallet_score circular logic blocker)
  - fill_probability: execution uncertainty discount (no spot feed = lower fill prob)
  - liquidity_factor: depth-based execution haircut
  - confidence_adjusted_edge = net_edge × confidence × fill_probability × liquidity_factor

Without layer 3, high-edge trades lose because the model is overconfident.
With layer 3, edge_at_decision reflects what can actually be captured.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os

# Edge inflation guard: caps raw probability divergence.
# Rationale: without spot feed, wallet_score drives Bayesian → inflated prob divergence.
# Any raw_edge above this cap is almost certainly model overconfidence, not real alpha.
MAX_RAW_EDGE = float(os.getenv("MAX_RAW_EDGE", "0.15"))

# Execution probability when no live spot feed is connected.
# Realistic fill rate given uncertain model state.
BASE_FILL_PROBABILITY = float(os.getenv("BASE_FILL_PROBABILITY", "0.70"))


@dataclass
class EdgeResult:
    raw_edge: float
    net_edge: float
    costs_breakdown: dict
    confidence_adjusted_edge: float
    executable_edge: float        # Layer 3: final tradable edge after all haircuts
    is_actionable: bool


def calculate_polymarket_fee(price: float, fee_rate: float = 0.25, exponent: float = 2.0) -> float:
    """Polymarket's fee formula: fee = C * p * feeRate * (p * (1-p))^exponent

    Fee peaks around p=0.5 and drops near 0 or 1.
    C is a scaling constant; for simplicity we use C=2 as Polymarket does.
    """
    if price <= 0 or price >= 1:
        return 0
    C = 2.0
    return C * price * fee_rate * (price * (1 - price)) ** exponent


def calculate_edge(
    model_probability: float,
    market_price: float,
    model_confidence: float,
    fees_enabled: bool = True,
    fee_rate_bps: int = 250,
    spread: float = 0.02,
    estimated_slippage: float = 0.005,
    fill_risk_cost: float = 0.003,
    available_depth_usd: float = 200.0,   # Layer 3 liquidity input
    fill_probability: float | None = None,  # None = use BASE_FILL_PROBABILITY
) -> EdgeResult:
    """Compute edge across 3 layers.

    Layer 1 — Fair Probability (raw_edge):
        raw_edge = clamp(model_probability − market_price, −MAX_RAW_EDGE, +MAX_RAW_EDGE)
        Edge cap prevents wallet_score circular logic from inflating probability divergence.

    Layer 2 — Tradable Edge (net_edge):
        net_edge = raw_edge − fee − spread/2 − slippage − fill_risk

    Layer 3 — Executable Edge (executable_edge):
        liquidity_factor = min(1.0, depth / 500)   # full credit at $500 depth
        executable_edge  = net_edge × confidence × fill_prob × liquidity_factor

    Args:
        model_probability: Bayesian estimate (q)
        market_price: current market price (p)
        model_confidence: [0, 1] trust in the model
        available_depth_usd: order book depth in USD (liquidity factor input)
        fill_probability: override fill probability; None uses BASE_FILL_PROBABILITY
    """
    q = model_probability
    p = market_price

    # ── Layer 1: Fair Probability → raw_edge with cap ─────────────────────────
    raw_edge_uncapped = q - p
    raw_edge = max(-MAX_RAW_EDGE, min(MAX_RAW_EDGE, raw_edge_uncapped))

    # Track how much was capped (useful for diagnostics)
    edge_capped = abs(raw_edge_uncapped) > MAX_RAW_EDGE

    # ── Layer 2: Tradable Edge — subtract market structure costs ──────────────
    if fees_enabled:
        fee = calculate_polymarket_fee(p, fee_rate=fee_rate_bps / 1000)
    else:
        fee = 0.0

    spread_cost = spread / 2
    total_cost = fee + spread_cost + estimated_slippage + fill_risk_cost
    net_edge = raw_edge - total_cost

    # ── Layer 3: Executable Edge — execution realism haircuts ─────────────────
    # Liquidity factor: deep markets get full credit, shallow markets are discounted
    liquidity_factor = min(1.0, available_depth_usd / 500.0)
    liquidity_factor = max(0.3, liquidity_factor)   # floor at 30% (never zero)

    # Fill probability: base rate adjusted by model confidence
    # When confidence is low (no spot feed), fill prob is lowered further
    fp = fill_probability if fill_probability is not None else BASE_FILL_PROBABILITY
    # Confidence gates the fill probability: high conf → full fill prob
    effective_fill_prob = fp * max(0.5, model_confidence)

    # confidence_adjusted (layer 2.5): used as the primary filter gate
    confidence_adjusted = net_edge * model_confidence

    # executable_edge (layer 3): full model
    executable_edge = net_edge * model_confidence * effective_fill_prob * liquidity_factor

    costs = {
        "fee": round(fee, 6),
        "spread": round(spread_cost, 6),
        "slippage_est": round(estimated_slippage, 6),
        "fill_risk": round(fill_risk_cost, 6),
        "total": round(total_cost, 6),
        "raw_edge_uncapped": round(raw_edge_uncapped, 6),
        "edge_was_capped": edge_capped,
        "liquidity_factor": round(liquidity_factor, 4),
        "fill_probability": round(effective_fill_prob, 4),
    }

    return EdgeResult(
        raw_edge=round(raw_edge, 6),
        net_edge=round(net_edge, 6),
        costs_breakdown=costs,
        confidence_adjusted_edge=round(confidence_adjusted, 6),
        executable_edge=round(executable_edge, 6),
        is_actionable=executable_edge > 0,
    )
