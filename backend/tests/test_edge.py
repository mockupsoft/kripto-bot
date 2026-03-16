"""Tests for the Edge calculator."""

from app.signals.edge import calculate_edge, calculate_polymarket_fee


def test_positive_edge():
    result = calculate_edge(
        model_probability=0.60,
        market_price=0.50,
        model_confidence=0.8,
        fees_enabled=True,
    )
    assert result.raw_edge > 0
    assert result.is_actionable


def test_negative_edge_not_actionable():
    result = calculate_edge(
        model_probability=0.51,
        market_price=0.50,
        model_confidence=0.5,
        fees_enabled=True,
    )
    assert not result.is_actionable


def test_costs_breakdown_present():
    result = calculate_edge(0.60, 0.50, 0.8)
    assert "fee" in result.costs_breakdown
    assert "spread" in result.costs_breakdown
    assert "slippage_est" in result.costs_breakdown
    assert "total" in result.costs_breakdown


def test_fee_peaks_at_50pct():
    fee_50 = calculate_polymarket_fee(0.50)
    fee_20 = calculate_polymarket_fee(0.20)
    fee_80 = calculate_polymarket_fee(0.80)
    assert fee_50 > fee_20
    assert fee_50 > fee_80


def test_no_fees_when_disabled():
    result = calculate_edge(0.60, 0.50, 0.8, fees_enabled=False)
    assert result.costs_breakdown["fee"] == 0
