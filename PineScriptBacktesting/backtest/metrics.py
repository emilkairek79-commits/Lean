"""Performance metrics matching the shape of TradingView's Strategy Tester
"Overview"/"Performance summary" tab."""

from __future__ import annotations

import math
from typing import Dict

from .results import BacktestResult


def compute_metrics(result: BacktestResult) -> Dict[str, float]:
    closed = [t for t in result.trades if t.is_closed]
    n = len(closed)
    equity_curve = result.equity_curve

    gross_profit = sum(t.pnl for t in closed if t.pnl and t.pnl > 0)
    gross_loss = -sum(t.pnl for t in closed if t.pnl and t.pnl < 0)
    net_profit = sum(t.pnl for t in closed if t.pnl is not None)
    wins = [t for t in closed if t.pnl and t.pnl > 0]
    losses = [t for t in closed if t.pnl and t.pnl < 0]

    final_equity = equity_curve[-1].equity if equity_curve else result.initial_capital
    max_drawdown, max_drawdown_pct = _max_drawdown(equity_curve, result.initial_capital)

    daily_returns = _bar_returns(equity_curve)
    sharpe = _sharpe_ratio(daily_returns)

    metrics = {
        "net_profit": net_profit,
        "net_profit_pct": (net_profit / result.initial_capital * 100.0) if result.initial_capital else 0.0,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0),
        "max_drawdown": max_drawdown,
        "max_drawdown_pct": max_drawdown_pct,
        "total_trades": n,
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "percent_profitable": (len(wins) / n * 100.0) if n else 0.0,
        "avg_trade": (net_profit / n) if n else 0.0,
        "avg_win": (sum(t.pnl for t in wins) / len(wins)) if wins else 0.0,
        "avg_loss": (sum(t.pnl for t in losses) / len(losses)) if losses else 0.0,
        "largest_win": max((t.pnl for t in wins), default=0.0),
        "largest_loss": min((t.pnl for t in losses), default=0.0),
        "sharpe_ratio": sharpe,
        "final_equity": final_equity,
    }
    return metrics


def _max_drawdown(equity_curve, initial_capital):
    peak = initial_capital
    max_dd = 0.0
    max_dd_pct = 0.0
    for point in equity_curve:
        peak = max(peak, point.equity)
        dd = peak - point.equity
        dd_pct = (dd / peak * 100.0) if peak else 0.0
        max_dd = max(max_dd, dd)
        max_dd_pct = max(max_dd_pct, dd_pct)
    return max_dd, max_dd_pct


def _bar_returns(equity_curve):
    returns = []
    for prev, cur in zip(equity_curve, equity_curve[1:]):
        if prev.equity:
            returns.append((cur.equity - prev.equity) / prev.equity)
    return returns


def _sharpe_ratio(returns, periods_per_year: int = 252) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(variance)
    if std == 0:
        return 0.0
    return (mean / std) * math.sqrt(periods_per_year)
