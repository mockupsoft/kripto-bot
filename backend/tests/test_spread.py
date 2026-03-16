"""Tests for the Spread detector."""

from app.signals.spread import SpreadDetector


def test_no_dislocation_normal_spread():
    det = SpreadDetector(z_threshold=2.0)
    for i in range(20):
        result = det.update("r1", "m1", "m2", 0.50 + 0.001 * (i % 3), 0.48)
    assert not result.is_dislocation


def test_dislocation_detected_on_large_spread():
    det = SpreadDetector(window_size=20, z_threshold=2.0)
    # Build normal history
    for _ in range(20):
        det.update("r1", "m1", "m2", 0.50, 0.48)
    # Inject large dislocation
    result = det.update("r1", "m1", "m2", 0.60, 0.40)
    assert result.is_dislocation
    assert abs(result.z_score) > 2.0


def test_provided_baseline_stats():
    det = SpreadDetector()
    result = det.update("r1", "m1", "m2", 0.55, 0.45, normal_spread_mean=0.02, normal_spread_std=0.01)
    # Spread = 0.10, mean = 0.02, std = 0.01 => z = 8.0
    assert result.is_dislocation
    assert result.z_score > 5
