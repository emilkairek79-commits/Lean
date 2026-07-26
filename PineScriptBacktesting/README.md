# Pine Script AI Backtesting Engine

A standalone backtesting engine that converts a subset of Pine Script
(TradingView's strategy language, v5/v6) into Python and runs it bar-by-bar
against OHLCV data, producing trades, an equity curve, and a TradingView-style
performance summary (net profit, profit factor, max drawdown, % profitable,
Sharpe ratio, etc).

It has no third-party dependencies - everything here runs with a plain
Python 3 standard library install (no pandas, no pip install required).

## Layout

```
PineScriptBacktesting/
  pinescript/       Pine Script lexer, parser, AST, interpreter, ta.* library
  backtest/          Broker/order simulation, backtest engine, metrics
  examples/          Example strategy + sample OHLCV data
  tests/             Unit + integration tests (stdlib unittest, no pytest needed)
  cli.py             Command-line runner
```

## Quick start

```bash
cd PineScriptBacktesting
python3 cli.py examples/sma_crossover.pine examples/data/sample_ohlcv.csv
```

Or from Python:

```python
from backtest.engine import run_backtest, load_ohlcv_csv

bars = load_ohlcv_csv("examples/data/sample_ohlcv.csv")
source = open("examples/sma_crossover.pine").read()
result = run_backtest(source, bars)

print(result.metrics)          # dict of summary stats
for trade in result.trades:    # full trade list
    print(trade)
```

`bars` is just a list of dicts with `time/open/high/low/close/volume` keys,
oldest-first - swap `load_ohlcv_csv` for your own data source (a TradingView
export, a broker API, etc.) as long as you produce that shape.

Run the test suite with:

```bash
python3 -m unittest discover -s tests
```

## What it supports

**Pine language**: `strategy()` declarations, `var`/`varip` persistent
variables, `:=` reassignment, `if/else` (as a statement or a multi-line
expression), `for` loops (with `break`/`continue`), single-line and
multi-line function definitions (with `return`), tuple destructuring
(`[a, b, c] = ta.macd(...)`), the usual operators with Pine's precedence,
history references (`close[1]`), `na`/`nz()`, and `input.*` (returns the
declared default - there's no UI, so this is how you parameterize a
strategy from Python).

**Indicators (`ta.*`)**: `sma`, `ema`, `rma`, `wma`, `vwma`, `stdev`,
`variance`, `highest`/`lowest` (+ `*bars`), `rsi`, `atr`/`tr`, `stoch`,
`macd`, `bb`/`bbands`, `cross`/`crossover`/`crossunder`, `change`, `cum`,
`valuewhen`, `barssince`. Plus `math.*` helpers (`abs`, `min`, `max`,
`round`, `floor`, `ceil`, `pow`, `sqrt`, `log`, `log10`, `sign`, `avg`).

**Strategy orders**: `strategy.entry`, `strategy.order`, `strategy.exit`
(profit/loss points, absolute `limit`/`stop` prices, and a basic
`trail_points`/`trail_offset` trailing stop), `strategy.close`,
`strategy.close_all`, `strategy.cancel[_all]`, plus `pyramiding`, three
commission models (`percent`, `cash_per_contract`, `cash_per_order`), and
three position-sizing models (`fixed`, `cash`, `percent_of_equity`).

**Visual-only calls** (`plot*`, `bgcolor`, `fill`, `hline`, `alert*`,
`label.*`, `line.*`, `table.*`, `box.*`) are accepted and evaluated as
no-ops, since a headless backtest has nothing to draw them on - they won't
break conversion of a strategy that also plots things for the chart.

## Known limitations

These mirror the physical limits of backtesting from OHLC bars instead of
tick data, plus this engine's current language coverage:

- **Slippage is always 0.** Modeling real slippage needs tick-by-tick
  data, which isn't available here.
- **No "bar magnifier."** TP/SL and limit/stop orders are resolved against
  each bar's OHLC range, not intrabar tick sequence. If a bar's high and
  low both touch a bracket's take-profit *and* stop-loss in the same bar,
  which one "actually" hit first is unknowable without tick data; this
  engine approximates it by picking whichever price is closer to the
  bar's open.
- **Not every Pine feature converts.** `request.security()` (multi-timeframe/
  multi-symbol data), `import` of community libraries, user-defined types
  (`type ...`), matrices/maps, and `switch`/`while` are not supported and
  raise a clear `PineUnsupportedError` rather than silently producing wrong
  results. Most single-symbol, single-timeframe strategies built from
  standard indicators convert fine.
- **Simplified variable scoping.** Every variable lives in one flat,
  per-script namespace rather than Pine's real block scoping. This matches
  real Pine behavior for the common pattern of a `var`-declared variable
  reassigned with `:=` inside an `if`, but a plain `x = expr` written only
  inside a conditional branch will retain its last-computed value on bars
  where that branch doesn't run, instead of Pine's actual per-scope
  redeclaration.
- **Netting position model.** Positions are netted (like TradingView's
  default account mode): pyramiding controls how many same-direction
  entries can stack, and an opposite-direction `strategy.entry` first
  flattens the existing position, then opens the remainder. Per-entry-ID
  bracket accounting for multiple simultaneously open, unmerged entries
  isn't modeled.
- **No `ta.*` calls inside loops or multiply-invoked functions.** Each
  `ta.*` call site keeps its own persistent state, fed once per bar - the
  same restriction Pine itself imposes on `ta.*` inside `for`/`while`.

Rounding differences versus TradingView's own numbers can still occur, but
they shouldn't change a strategy's overall verdict (profitable vs. not,
which of two variants performs better, etc).
