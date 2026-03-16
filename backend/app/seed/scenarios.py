"""Pre-built scenario configurations for the demo seed data."""

SCENARIOS = {
    "fast_whale": {
        "description": "High ROI wallet whose edge disappears within 200ms. Uncopyable.",
        "wallet_label": "Fast Whale",
        "expected_copy_decay": {"200ms": 0.12, "500ms": -0.03, "1000ms": -0.08, "1500ms": -0.11},
        "expected_raw_roi": 0.42,
        "expected_copyable_roi_at_800ms": -0.02,
    },
    "steady_eddie": {
        "description": "Consistent 58% win-rate wallet. Edge persists even at 1500ms delay.",
        "wallet_label": "Steady Eddie",
        "expected_copy_decay": {"200ms": 0.91, "500ms": 0.84, "1000ms": 0.72, "1500ms": 0.61},
        "expected_raw_roi": 0.18,
        "expected_copyable_roi_at_800ms": 0.14,
    },
    "dislocation_window": {
        "description": "BTC 5m vs 15m spread exceeds z=2.5. Net edge 2.1% after costs.",
        "market_slugs": ["btc-up-5m-window-1", "btc-up-15m-window-1"],
        "expected_spread_z": 2.5,
        "expected_net_edge": 0.021,
    },
    "trap": {
        "description": "Wallet appears profitable for 20 trades then catastrophically loses.",
        "wallet_label": "Trap Wallet",
        "expected_consistency_score": 0.25,
        "expected_suspiciousness_score": 0.72,
    },
    "ghost_liquidity": {
        "description": "Dislocation with thin book. Partial fill eats most of the edge.",
        "market_slugs": ["btc-up-5m-window-1", "btc-up-15m-window-1"],
        "expected_raw_edge": 0.045,
        "expected_fill_pct": 0.60,
        "expected_net_realized": 0.94,
    },
}
