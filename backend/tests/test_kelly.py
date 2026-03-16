"""Tests for the Kelly Criterion position sizer."""

from app.risk.kelly import kelly_size


def test_positive_edge_returns_nonzero_size():
    result = kelly_size(win_probability=0.6, payoff_ratio=1.0)
    assert result.proposed_size_usd > 0


def test_negative_edge_returns_zero():
    result = kelly_size(win_probability=0.3, payoff_ratio=1.0)
    assert result.proposed_size_usd == 0


def test_fractional_kelly_smaller_than_full():
    full = kelly_size(win_probability=0.6, payoff_ratio=1.0, kelly_fraction=1.0)
    quarter = kelly_size(win_probability=0.6, payoff_ratio=1.0, kelly_fraction=0.25)
    assert quarter.proposed_size_usd < full.proposed_size_usd


def test_capped_at_max_position():
    result = kelly_size(
        win_probability=0.9,
        payoff_ratio=5.0,
        kelly_fraction=1.0,
        max_position_pct=0.08,
        available_bankroll=1000.0,
    )
    assert result.proposed_size_usd <= 80.0


def test_confidence_reduces_size():
    high_conf = kelly_size(win_probability=0.6, payoff_ratio=1.0, model_confidence=1.0)
    low_conf = kelly_size(win_probability=0.6, payoff_ratio=1.0, model_confidence=0.3)
    assert low_conf.proposed_size_usd < high_conf.proposed_size_usd
