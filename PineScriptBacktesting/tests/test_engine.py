import unittest

from backtest.engine import run_backtest


SMA_CROSS = """
//@version=5
strategy("SMA Cross", overlay=true, initial_capital=10000,
     default_qty_type=strategy.fixed, default_qty_value=10,
     commission_type=strategy.commission.percent, commission_value=0.0)

fastMA = ta.sma(close, 2)
slowMA = ta.sma(close, 4)

if ta.crossover(fastMA, slowMA)
    strategy.entry("Long", strategy.long)

if ta.crossunder(fastMA, slowMA)
    strategy.entry("Short", strategy.short)
"""


def make_bars(closes):
    bars = []
    for i, c in enumerate(closes):
        bars.append({"time": i, "open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 100.0})
    return bars


class TestEngine(unittest.TestCase):
    def test_hand_crafted_crossover_produces_expected_trade(self):
        # Prices dip (crossunder) then rally hard enough to force a clean
        # crossover back up.
        closes = [10, 10, 10, 10, 9, 8, 7, 12, 16, 20, 20, 20]
        bars = make_bars(closes)
        result = run_backtest(SMA_CROSS, bars)
        self.assertEqual(len(result.trades), 2)
        self.assertEqual(result.trades[0].direction, "short")
        self.assertEqual(result.trades[1].direction, "long")
        self.assertEqual(result.trades[0].qty, 10.0)

    def test_zero_commission_zero_slippage_pnl_matches_price_delta(self):
        closes = [10, 10, 10, 10, 9, 8, 7, 12, 16, 20, 20, 20, 20, 19, 18, 12, 8, 4]
        bars = make_bars(closes)
        result = run_backtest(SMA_CROSS, bars)
        for trade in result.trades:
            if trade.is_closed:
                sign = 1 if trade.direction == "long" else -1
                expected = (trade.exit_price - trade.entry_price) * trade.qty * sign
                self.assertAlmostEqual(trade.pnl, expected, places=6)

    def test_equity_curve_length_matches_bars(self):
        closes = [10, 11, 12, 11, 10, 9, 10, 11]
        bars = make_bars(closes)
        result = run_backtest(SMA_CROSS, bars)
        self.assertEqual(len(result.equity_curve), len(bars))

    def test_metrics_are_internally_consistent(self):
        closes = [10, 10, 10, 10, 9, 8, 7, 12, 16, 20, 20, 20, 20, 19, 18, 12, 8, 4]
        bars = make_bars(closes)
        result = run_backtest(SMA_CROSS, bars)
        m = result.metrics
        self.assertAlmostEqual(m["gross_profit"] - m["gross_loss"], m["net_profit"], places=6)
        self.assertEqual(m["winning_trades"] + m["losing_trades"] <= m["total_trades"], True)

    def test_take_profit_stop_loss_bracket(self):
        src = """
//@version=5
strategy("TP/SL", initial_capital=10000, default_qty_type=strategy.fixed, default_qty_value=1,
     commission_type=strategy.commission.percent, commission_value=0.0)

if bar_index == 0
    strategy.entry("Long", strategy.long)
    strategy.exit("Bracket", "Long", profit=5, loss=3)
"""
        # entry fills at bar1 open=100; bar3's high touches take-profit (105).
        bars = [
            {"time": 0, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
            {"time": 1, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
            {"time": 2, "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1},
            {"time": 3, "open": 101, "high": 106, "low": 100, "close": 105, "volume": 1},
            {"time": 4, "open": 105, "high": 106, "low": 104, "close": 105, "volume": 1},
        ]
        result = run_backtest(src, bars)
        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertAlmostEqual(trade.exit_price, 105.0)
        self.assertAlmostEqual(trade.pnl, 5.0)


if __name__ == "__main__":
    unittest.main()
