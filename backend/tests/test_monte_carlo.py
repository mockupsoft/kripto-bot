"""Tests for the Monte Carlo simulator."""

from app.simulation.monte_carlo import run_monte_carlo


def test_deterministic_with_seed():
    r1 = run_monte_carlo(n_simulations=50, random_seed=42)
    r2 = run_monte_carlo(n_simulations=50, random_seed=42)
    assert r1.median_final_equity == r2.median_final_equity
    assert r1.ruin_probability == r2.ruin_probability


def test_percentiles_ordered():
    result = run_monte_carlo(n_simulations=100, random_seed=42)
    p = result.percentiles
    assert p["P5"] <= p["P25"] <= p["P50"] <= p["P75"] <= p["P95"]


def test_high_win_prob_profitable():
    result = run_monte_carlo(
        n_simulations=200, win_probability=0.7,
        avg_win_pct=0.05, avg_fee_pct=0.002, avg_slippage_pct=0.001,
        random_seed=42,
    )
    assert result.median_final_equity > result.percentiles.get("P5", 0)


def test_low_win_prob_loses_capital():
    from app.simulation.monte_carlo import run_monte_carlo as _mc
    result = _mc(n_simulations=200, win_probability=0.3, random_seed=42)
    assert result.median_final_equity < 5000
