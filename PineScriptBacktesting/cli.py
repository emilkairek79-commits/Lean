#!/usr/bin/env python3
"""Command-line entry point: run a Pine Script strategy against a CSV of
OHLCV bars and print a TradingView-style performance summary.

Usage:
    python3 cli.py path/to/strategy.pine path/to/data.csv [--capital 10000] [--trades-csv out.csv]
"""

from __future__ import annotations

import argparse
import csv
import sys

from backtest.engine import load_ohlcv_csv, run_backtest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Pine Script AI backtesting engine")
    parser.add_argument("strategy", help="Path to a .pine Pine Script v5/v6 strategy file")
    parser.add_argument("data", help="Path to a CSV file with time,open,high,low,close,volume columns")
    parser.add_argument("--capital", type=float, default=None, help="Override strategy's initial_capital")
    parser.add_argument("--trades-csv", default=None, help="Optional path to write the trade list as CSV")
    args = parser.parse_args(argv)

    with open(args.strategy) as f:
        source = f.read()
    bars = load_ohlcv_csv(args.data)

    result = run_backtest(source, bars, initial_capital_override=args.capital)

    print(f"Bars processed: {len(bars)}")
    print(f"Total trades:   {result.metrics['total_trades']}")
    print(f"Net profit:     {result.metrics['net_profit']:.2f} ({result.metrics['net_profit_pct']:.2f}%)")
    print(f"Gross profit:   {result.metrics['gross_profit']:.2f}")
    print(f"Gross loss:     {result.metrics['gross_loss']:.2f}")
    print(f"Profit factor:  {result.metrics['profit_factor']:.3f}")
    print(f"Max drawdown:   {result.metrics['max_drawdown']:.2f} ({result.metrics['max_drawdown_pct']:.2f}%)")
    print(f"% Profitable:   {result.metrics['percent_profitable']:.2f}%")
    print(f"Avg trade:      {result.metrics['avg_trade']:.2f}")
    print(f"Sharpe ratio:   {result.metrics['sharpe_ratio']:.3f}")
    print(f"Final equity:   {result.metrics['final_equity']:.2f}")

    if args.trades_csv:
        with open(args.trades_csv, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["entry_id", "direction", "qty", "entry_time", "entry_price",
                 "exit_time", "exit_price", "exit_comment", "pnl"]
            )
            for t in result.trades:
                writer.writerow(
                    [t.entry_id, t.direction, round(t.qty, 6), t.entry_time, t.entry_price,
                     t.exit_time, t.exit_price, t.exit_comment, t.pnl]
                )
        print(f"Trade list written to {args.trades_csv}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
