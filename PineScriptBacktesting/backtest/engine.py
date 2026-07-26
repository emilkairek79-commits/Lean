"""Top-level API: parse a Pine strategy, run it bar-by-bar against OHLCV
data, and return trades/equity/metrics."""

from __future__ import annotations

import csv
from typing import Any, Dict, List, Optional

from pinescript.interpreter import Interpreter
from pinescript.parser import parse

from .broker import Broker
from .metrics import compute_metrics
from .results import BacktestResult


def load_ohlcv_csv(path: str) -> List[Dict[str, Any]]:
    """Load bars from a CSV with columns: time,open,high,low,close,volume
    (volume optional, defaults to 0)."""
    bars: List[Dict[str, Any]] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            bars.append(
                {
                    "time": row.get("time") or row.get("date") or row.get("datetime"),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]) if row.get("volume") not in (None, "") else 0.0,
                }
            )
    return bars


def run_backtest(
    pine_source: str,
    bars: List[Dict[str, Any]],
    initial_capital_override: Optional[float] = None,
) -> BacktestResult:
    """Convert `pine_source` and run it against `bars`.

    `bars` is a list of dicts with keys time/open/high/low/close/volume,
    in chronological order (oldest first) - the same convention as
    TradingView chart data.
    """
    program = parse(pine_source)
    if not program.is_strategy:
        raise ValueError(
            "Only `strategy(...)` scripts can be backtested; this source "
            "declares `indicator(...)`."
        )

    # First pass: interpreter with no broker, just to statically read the
    # strategy() declaration's settings (title, initial_capital, etc.).
    settings_interp = Interpreter(program, bars[:1] or [{"open": 0, "high": 0, "low": 0, "close": 0}])
    settings = settings_interp.eval_declaration_kwargs()

    initial_capital = initial_capital_override or settings.get("initial_capital", 10000.0)
    broker = Broker(
        initial_capital=initial_capital,
        default_qty_type=settings.get("default_qty_type", "percent_of_equity"),
        default_qty_value=settings.get("default_qty_value", 100.0),
        commission_type=settings.get("commission_type"),
        commission_value=settings.get("commission_value", 0.0),
        pyramiding=int(settings.get("pyramiding", 1)),
    )

    interp = Interpreter(program, bars, broker=broker)

    for i, bar in enumerate(bars):
        broker.process_bar(i, bar.get("time"), bar["open"], bar["high"], bar["low"], bar["close"])
        interp.run_bar(i)
        broker.record_equity_point(i, bar.get("time"))

    # Close any position still open at the end of the data, at the last close,
    # so metrics reflect a fully realized run (TradingView does the same).
    if broker.position_qty > 0 and bars:
        last = bars[-1]
        broker._close_current_trade(len(bars) - 1, last.get("time"), last["close"], comment="end_of_data")
        broker._update_readable_state(last["close"])
        broker.result.equity_curve[-1] = broker.result.equity_curve[-1].__class__(
            bar=len(bars) - 1, time=last.get("time"), equity=broker.equity, open_profit=0.0
        )

    result = broker.result
    result.metrics = compute_metrics(result)
    return result
