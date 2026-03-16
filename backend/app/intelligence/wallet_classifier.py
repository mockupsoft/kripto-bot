"""Classify wallet behavior patterns: market_maker, arbitrageur, gambler, unknown."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.models.trade import WalletTransaction


@dataclass
class ClassificationResult:
    label: str
    confidence: float
    features: dict


def classify_wallet(txs: list[WalletTransaction]) -> ClassificationResult:
    if len(txs) < 5:
        return ClassificationResult("unknown", 0.0, {"reason": "insufficient_data"})

    features = _extract_features(txs)
    label, confidence = _apply_rules(features)

    return ClassificationResult(label=label, confidence=confidence, features=features)


def _extract_features(txs: list[WalletTransaction]) -> dict:
    prices = [float(t.price) for t in txs if t.price]
    sizes = [float(t.size) for t in txs if t.size]
    lags = [t.detection_lag_ms for t in txs if t.detection_lag_ms is not None]

    buy_count = sum(1 for t in txs if t.side == "BUY")
    sell_count = sum(1 for t in txs if t.side == "SELL")
    total = len(txs)

    unique_markets = len(set(str(t.market_id) for t in txs))

    times = sorted([t.occurred_at for t in txs if t.occurred_at])
    if len(times) > 1:
        intervals = [(times[i + 1] - times[i]).total_seconds() for i in range(len(times) - 1)]
        avg_interval = np.mean(intervals)
        interval_regularity = np.std(intervals) / (avg_interval + 1e-8) if intervals else 1.0
    else:
        avg_interval = 0
        interval_regularity = 1.0

    return {
        "trade_count": total,
        "buy_ratio": buy_count / total,
        "sell_ratio": sell_count / total,
        "avg_price": np.mean(prices) if prices else 0.5,
        "price_std": np.std(prices) if len(prices) > 1 else 0,
        "avg_size": np.mean(sizes) if sizes else 0,
        "size_cv": np.std(sizes) / (np.mean(sizes) + 1e-8) if sizes else 0,
        "avg_lag_ms": np.mean(lags) if lags else 0,
        "unique_markets": unique_markets,
        "market_diversity": unique_markets / total,
        "avg_interval_s": avg_interval,
        "interval_regularity": interval_regularity,
    }


def _apply_rules(f: dict) -> tuple[str, float]:
    """Rule-based classification. Bayesian or ML could replace this later."""

    # Market maker: trades both sides, regular intervals, many markets
    if f["buy_ratio"] > 0.3 and f["sell_ratio"] > 0.3 and f["interval_regularity"] < 0.5:
        return "market_maker", 0.7

    # Arbitrageur: fast, small price range, many markets, low lag
    if f["avg_lag_ms"] < 500 and f["price_std"] < 0.1 and f["unique_markets"] > 3:
        return "arbitrageur", 0.65

    # Gambler: high variance, few markets, erratic timing
    if f["size_cv"] > 1.5 and f["unique_markets"] <= 3:
        return "gambler", 0.6

    # Persistent trader: consistent sizes, steady pace
    if f["size_cv"] < 0.5 and f["trade_count"] > 20:
        return "arbitrageur", 0.5

    return "unknown", 0.3
