"""Tests for the Bayesian probability estimator."""

from app.signals.bayesian import BayesianEstimator


def test_initial_estimate_near_50pct():
    est = BayesianEstimator(prior_alpha=2.0, prior_beta=2.0)
    assert abs(est.current_estimate - 0.5) < 0.01


def test_update_shifts_probability():
    est = BayesianEstimator()
    result = est.update(
        spot_move_direction=0.8,
        volatility_signal=0.3,
        order_book_imbalance=0.5,
        repricing_speed=0.6,
    )
    assert result.model_probability > 0.5
    assert 0 < result.confidence < 1


def test_negative_signal_lowers_probability():
    est = BayesianEstimator()
    result = est.update(
        spot_move_direction=-0.8,
        volatility_signal=0.3,
        order_book_imbalance=-0.5,
        repricing_speed=0.6,
    )
    assert result.model_probability < 0.5


def test_confidence_increases_with_data():
    est = BayesianEstimator()
    c1 = est.current_confidence
    for _ in range(10):
        est.update(0.5, 0.2, 0.3, 0.4)
    c2 = est.current_confidence
    assert c2 > c1


def test_aging_reduces_confidence():
    est = BayesianEstimator()
    est.update(0.5, 0.2, 0.3, 0.4)
    c_before = est.current_confidence
    est.age_estimate(300)
    c_after = est.current_confidence
    assert c_after <= c_before
