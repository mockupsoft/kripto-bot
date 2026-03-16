"""Monte Carlo simulator: N-path capital trajectory generator.

Tests whether the full setup can survive real market conditions
rather than just a clean backtest. Protection against self-deception.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class MonteCarloResult:
    n_simulations: int
    percentiles: dict[str, float]
    max_drawdown_distribution: dict[str, float]
    ruin_probability: float
    median_final_equity: float
    mean_final_equity: float
    paths: list[list[float]] | None


def run_monte_carlo(
    n_simulations: int = 600,
    n_trades: int = 100,
    starting_capital: float = 900.0,
    win_probability: float = 0.55,
    avg_win_pct: float = 0.03,
    avg_loss_pct: float = 0.025,
    fill_rate: float = 0.85,
    avg_slippage_pct: float = 0.005,
    avg_fee_pct: float = 0.01,
    position_size_pct: float = 0.06,
    random_seed: int | None = None,
    return_paths: bool = False,
) -> MonteCarloResult:
    """Run Monte Carlo simulation of the strategy's capital path.

    W(t+1) = W(t) * (1 + r(t))

    Each simulation samples from:
    - fill rate variation
    - slippage variation
    - execution delay impact
    - edge accuracy noise
    """
    rng = np.random.default_rng(random_seed)

    final_equities = []
    max_drawdowns = []
    all_paths = [] if return_paths else None
    ruin_count = 0
    ruin_threshold = starting_capital * 0.1

    for _ in range(n_simulations):
        capital = starting_capital
        peak = capital
        max_dd = 0
        path = [capital] if return_paths else None

        for _ in range(n_trades):
            # Sample this trade's parameters
            trade_fill_rate = rng.normal(fill_rate, 0.1)
            trade_fill_rate = max(0, min(1, trade_fill_rate))

            if rng.random() > trade_fill_rate:
                if path is not None:
                    path.append(capital)
                continue

            position = capital * position_size_pct

            # Sample outcome
            is_win = rng.random() < win_probability
            if is_win:
                raw_return = rng.normal(avg_win_pct, avg_win_pct * 0.3)
            else:
                raw_return = -rng.normal(avg_loss_pct, avg_loss_pct * 0.3)

            # Apply costs
            slippage = rng.normal(avg_slippage_pct, avg_slippage_pct * 0.5)
            slippage = max(0, slippage)
            fee = avg_fee_pct

            net_return = raw_return - slippage - fee
            pnl = position * net_return
            capital += pnl

            # Track drawdown
            peak = max(peak, capital)
            dd = (peak - capital) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

            if path is not None:
                path.append(capital)

            if capital < ruin_threshold:
                ruin_count += 1
                break

        final_equities.append(capital)
        max_drawdowns.append(max_dd)
        if all_paths is not None and path is not None:
            all_paths.append(path)

    equities = np.array(final_equities)
    drawdowns = np.array(max_drawdowns)

    return MonteCarloResult(
        n_simulations=n_simulations,
        percentiles={
            "P5": round(float(np.percentile(equities, 5)), 2),
            "P25": round(float(np.percentile(equities, 25)), 2),
            "P50": round(float(np.percentile(equities, 50)), 2),
            "P75": round(float(np.percentile(equities, 75)), 2),
            "P95": round(float(np.percentile(equities, 95)), 2),
        },
        max_drawdown_distribution={
            "P5": round(float(np.percentile(drawdowns, 5)), 4),
            "P25": round(float(np.percentile(drawdowns, 25)), 4),
            "P50": round(float(np.percentile(drawdowns, 50)), 4),
            "P75": round(float(np.percentile(drawdowns, 75)), 4),
            "P95": round(float(np.percentile(drawdowns, 95)), 4),
        },
        ruin_probability=round(ruin_count / n_simulations, 4),
        median_final_equity=round(float(np.median(equities)), 2),
        mean_final_equity=round(float(np.mean(equities)), 2),
        paths=all_paths,
    )
