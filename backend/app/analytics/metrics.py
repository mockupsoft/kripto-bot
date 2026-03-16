"""Aggregate metrics: Sharpe, drawdown, win rate, profit factor, etc."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class StrategyMetrics:
    total_trades: int
    win_rate: float
    gross_pnl: float
    net_pnl: float
    total_fees: float
    total_slippage: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    max_drawdown: float
    sharpe_ratio: float
    avg_adverse_excursion: float
    avg_favorable_excursion: float


def compute_metrics(trade_pnls: list[float], trade_fees: list[float], trade_slippages: list[float]) -> StrategyMetrics:
    if not trade_pnls:
        return StrategyMetrics(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    wins = [p for p in trade_pnls if p > 0]
    losses = [p for p in trade_pnls if p <= 0]

    gross = sum(trade_pnls)
    fees = sum(trade_fees)
    slippage = sum(trade_slippages)
    net = gross - fees - slippage

    avg_win = np.mean(wins) if wins else 0
    avg_loss = np.mean(losses) if losses else 0

    total_wins = sum(wins)
    total_losses = abs(sum(losses))
    pf = total_wins / total_losses if total_losses > 0 else float("inf")

    # Max drawdown from cumulative PnL
    cumulative = np.cumsum(trade_pnls)
    peak = np.maximum.accumulate(cumulative)
    dd = (peak - cumulative)
    max_dd = float(np.max(dd)) if len(dd) > 0 else 0

    # Simplified Sharpe: mean return / std return (annualized with daily trades)
    if len(trade_pnls) > 1 and np.std(trade_pnls) > 0:
        sharpe = float(np.mean(trade_pnls) / np.std(trade_pnls) * np.sqrt(252))
    else:
        sharpe = 0

    return StrategyMetrics(
        total_trades=len(trade_pnls),
        win_rate=len(wins) / len(trade_pnls),
        gross_pnl=round(gross, 4),
        net_pnl=round(net, 4),
        total_fees=round(fees, 4),
        total_slippage=round(slippage, 4),
        avg_win=round(float(avg_win), 4),
        avg_loss=round(float(avg_loss), 4),
        profit_factor=round(pf, 4),
        max_drawdown=round(max_dd, 4),
        sharpe_ratio=round(sharpe, 4),
        avg_adverse_excursion=round(abs(float(avg_loss)), 4),
        avg_favorable_excursion=round(float(avg_win), 4),
    )
