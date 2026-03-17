"""Probability calibration utilities (hybrid linear + isotonic fallback)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from bisect import bisect_right

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


NON_TRADING_EXITS = ("stale_data", "position_cap_cleanup", "demo_cleanup")


@dataclass
class CalibrationModel:
    method: str
    sample_size: int
    slope: float
    intercept: float
    iso_x: list[float]
    iso_y: list[float]
    fitted_at: datetime

    def apply(self, p: float) -> float:
        p_clamped = max(0.001, min(0.999, float(p)))
        if self.method == "isotonic" and self.iso_x and self.iso_y:
            idx = bisect_right(self.iso_x, p_clamped) - 1
            idx = max(0, min(idx, len(self.iso_y) - 1))
            return max(0.001, min(0.999, self.iso_y[idx]))
        out = self.slope * p_clamped + self.intercept
        return max(0.001, min(0.999, out))


def _fit_linear(points: list[tuple[float, int]]) -> tuple[float, float]:
    """Fit y = a*x + b with simple OLS; fallback to identity when unstable."""
    n = len(points)
    if n < 2:
        return 1.0, 0.0
    sx = sum(p for p, _y in points)
    sy = sum(y for _p, y in points)
    sxx = sum(p * p for p, _y in points)
    sxy = sum(p * y for p, y in points)
    den = n * sxx - sx * sx
    if abs(den) < 1e-12:
        return 1.0, 0.0
    a = (n * sxy - sx * sy) / den
    b = (sy - a * sx) / n
    # keep linear fallback sane; avoid aggressive overfitting
    a = max(0.2, min(2.0, a))
    b = max(-0.5, min(0.5, b))
    return a, b


def _fit_isotonic(points: list[tuple[float, int]]) -> tuple[list[float], list[float]]:
    """
    Lightweight isotonic regression via PAV (pool adjacent violators).
    Returns stepwise x/y vectors for inference.
    """
    if not points:
        return [], []
    pts = sorted((float(p), int(y)) for p, y in points)
    blocks: list[dict] = []
    for p, y in pts:
        blocks.append({"sum_y": float(y), "count": 1.0, "x_max": p, "x_min": p})
        while len(blocks) >= 2:
            b2 = blocks[-1]
            b1 = blocks[-2]
            m1 = b1["sum_y"] / b1["count"]
            m2 = b2["sum_y"] / b2["count"]
            if m1 <= m2:
                break
            merged = {
                "sum_y": b1["sum_y"] + b2["sum_y"],
                "count": b1["count"] + b2["count"],
                "x_min": b1["x_min"],
                "x_max": b2["x_max"],
            }
            blocks = blocks[:-2]
            blocks.append(merged)

    iso_x: list[float] = []
    iso_y: list[float] = []
    for b in blocks:
        mean_y = max(0.001, min(0.999, b["sum_y"] / b["count"]))
        # step point at the block max x
        iso_x.append(float(b["x_max"]))
        iso_y.append(float(mean_y))
    return iso_x, iso_y


class HybridProbabilityCalibrator:
    """
    Hybrid calibrator:
      - default linear (a*p+b)
      - switch to isotonic when sample threshold is reached
    """

    def __init__(self):
        self._cache_ttl_sec = int(os.getenv("CALIBRATION_REFRESH_SEC", "300"))
        self._lookback_days = int(os.getenv("CALIBRATION_LOOKBACK_DAYS", "14"))
        self._min_samples = int(os.getenv("CALIBRATION_MIN_SAMPLES", "120"))
        self._min_samples_isotonic = int(os.getenv("CALIBRATION_MIN_SAMPLES_ISOTONIC", "300"))
        self._model: CalibrationModel | None = None

    async def calibrate(self, db: AsyncSession, p: float) -> tuple[float, str]:
        model = await self._get_model(db)
        return model.apply(p), model.method

    async def diagnostics(self, db: AsyncSession) -> dict:
        points = await self._load_points(db)
        if not points:
            return {
                "sample_size": 0,
                "method": "identity",
                "brier_score": None,
                "ece": None,
                "bucket_report": [],
            }
        model = await self._get_model(db, force_refresh=True)
        calibrated = [(model.apply(p), y) for p, y in points]
        brier = sum((pc - y) ** 2 for pc, y in calibrated) / len(calibrated)

        # ECE-like with fixed buckets
        bins = [(i / 20, (i + 1) / 20) for i in range(20)]  # 0.05 width
        ece = 0.0
        bucket_report: list[dict] = []
        for lo, hi in bins:
            vals = [(pc, y) for pc, y in calibrated if lo <= pc < hi or (hi == 1.0 and pc == 1.0)]
            if not vals:
                continue
            conf = sum(pc for pc, _y in vals) / len(vals)
            acc = sum(y for _pc, y in vals) / len(vals)
            gap = abs(conf - acc)
            ece += (len(vals) / len(calibrated)) * gap
            bucket_report.append(
                {
                    "bucket": f"{lo:.2f}-{hi:.2f}",
                    "count": len(vals),
                    "predicted_mean": round(conf, 4),
                    "actual_win_rate": round(acc, 4),
                    "abs_gap": round(gap, 4),
                }
            )
        return {
            "sample_size": len(points),
            "method": model.method,
            "brier_score": round(brier, 6),
            "ece": round(ece, 6),
            "bucket_report": bucket_report,
        }

    async def _get_model(self, db: AsyncSession, force_refresh: bool = False) -> CalibrationModel:
        now = datetime.now(timezone.utc)
        if not force_refresh and self._model:
            age = (now - self._model.fitted_at).total_seconds()
            if age < self._cache_ttl_sec:
                return self._model

        points = await self._load_points(db)
        if len(points) < self._min_samples:
            self._model = CalibrationModel(
                method="linear",
                sample_size=len(points),
                slope=1.0,
                intercept=0.0,
                iso_x=[],
                iso_y=[],
                fitted_at=now,
            )
            return self._model

        a, b = _fit_linear(points)
        method = "linear"
        iso_x: list[float] = []
        iso_y: list[float] = []
        if len(points) >= self._min_samples_isotonic:
            iso_x, iso_y = _fit_isotonic(points)
            method = "isotonic" if iso_x and iso_y else "linear"

        self._model = CalibrationModel(
            method=method,
            sample_size=len(points),
            slope=a,
            intercept=b,
            iso_x=iso_x,
            iso_y=iso_y,
            fitted_at=now,
        )
        return self._model

    async def _load_points(self, db: AsyncSession) -> list[tuple[float, int]]:
        since = datetime.now(timezone.utc) - timedelta(days=self._lookback_days)
        rows = await db.execute(
            text(
                """
                WITH linked AS (
                  SELECT DISTINCT ON (pp.id)
                    ts.model_probability AS predicted_prob,
                    CASE WHEN pp.realized_pnl > 0 THEN 1 ELSE 0 END AS outcome
                  FROM paper_positions pp
                  JOIN paper_orders po
                    ON po.market_id = pp.market_id
                   AND po.created_at BETWEEN pp.opened_at - INTERVAL '5 seconds'
                                         AND pp.opened_at + INTERVAL '5 seconds'
                  JOIN trade_signals ts
                    ON ts.id = po.signal_id
                  WHERE pp.status = 'closed'
                    AND pp.closed_at >= :since
                    AND pp.exit_reason NOT IN ('stale_data','position_cap_cleanup','demo_cleanup')
                    AND ts.model_probability IS NOT NULL
                  ORDER BY pp.id, po.created_at
                )
                SELECT predicted_prob, outcome
                FROM linked
                """
            ),
            {"since": since},
        )
        points: list[tuple[float, int]] = []
        for row in rows.mappings().all():
            p = float(row["predicted_prob"])
            y = int(row["outcome"])
            if 0.0 < p < 1.0:
                points.append((p, y))
        return points

