"""Tests for the Stoikov execution heuristic."""

from app.execution.stoikov import compute_execution_bias


def test_neutral_inventory_normal_bias():
    result = compute_execution_bias(mid_price=0.50, inventory_imbalance=0.0)
    assert result.urgency_level in ("passive", "normal")


def test_high_urgency_with_imbalance():
    result = compute_execution_bias(
        mid_price=0.50,
        inventory_imbalance=0.8,
        time_remaining_fraction=0.1,
    )
    assert result.bias > 0
    assert result.urgency_level in ("urgent", "critical")


def test_reservation_price_adjusts():
    neutral = compute_execution_bias(0.50, 0.0)
    long_bias = compute_execution_bias(0.50, 0.5)
    short_bias = compute_execution_bias(0.50, -0.5)
    assert long_bias.reservation_price < neutral.reservation_price
    assert short_bias.reservation_price > neutral.reservation_price
