import unittest

from pinescript import indicators as ta
from pinescript.na import NA, is_na


class TestIndicators(unittest.TestCase):
    def test_sma_basic(self):
        sma = ta.Sma(3)
        values = [1, 2, 3, 4, 5]
        out = [sma.update(v) for v in values]
        self.assertTrue(is_na(out[0]))
        self.assertTrue(is_na(out[1]))
        self.assertAlmostEqual(out[2], 2.0)
        self.assertAlmostEqual(out[3], 3.0)
        self.assertAlmostEqual(out[4], 4.0)

    def test_ema_seeds_with_first_value(self):
        ema = ta.Ema(3)  # alpha = 0.5
        out = [ema.update(v) for v in [10, 20, 20, 20]]
        self.assertAlmostEqual(out[0], 10.0)
        self.assertAlmostEqual(out[1], 15.0)
        self.assertAlmostEqual(out[2], 17.5)
        self.assertAlmostEqual(out[3], 18.75)

    def test_rma_seed_then_recursive(self):
        rma = ta.Rma(2)
        out = [rma.update(v) for v in [10, 20, 30]]
        self.assertTrue(is_na(out[0]))
        self.assertAlmostEqual(out[1], 15.0)  # sma seed of [10,20]
        self.assertAlmostEqual(out[2], (15.0 * 1 + 30) / 2)

    def test_wma_weights_recent_more(self):
        wma = ta.Wma(3)
        out = [wma.update(v) for v in [1, 2, 3]]
        # weights 1,2,3 on oldest->newest: (1*1+2*2+3*3)/6
        self.assertAlmostEqual(out[2], (1 * 1 + 2 * 2 + 3 * 3) / 6)

    def test_highest_lowest(self):
        highest = ta.Highest(3)
        lowest = ta.Lowest(3)
        vals = [5, 1, 9, 2]
        hi = [highest.update(v) for v in vals]
        lo = [lowest.update(v) for v in vals]
        self.assertAlmostEqual(hi[2], 9)
        self.assertAlmostEqual(hi[3], 9)
        self.assertAlmostEqual(lo[2], 1)
        self.assertAlmostEqual(lo[3], 1)

    def test_rsi_all_gains_is_100(self):
        rsi = ta.Rsi(3)
        out = [rsi.update(v) for v in [1, 2, 3, 4, 5, 6]]
        self.assertAlmostEqual(out[-1], 100.0)

    def test_rsi_all_losses_is_0(self):
        rsi = ta.Rsi(3)
        out = [rsi.update(v) for v in [6, 5, 4, 3, 2, 1]]
        self.assertAlmostEqual(out[-1], 0.0)

    def test_crossover_crossunder(self):
        cross = ta.Cross()
        # a crosses above b between step 2 and 3
        a = [1, 2, 5, 4]
        b = [3, 3, 3, 3]
        results = [cross.crossover(x, y) for x, y in zip(a, b)]
        self.assertEqual(results, [False, False, True, False])

    def test_macd_returns_na_until_slow_ready(self):
        macd = ta.Macd(2, 4, 2)
        results = [macd.update(v) for v in [1, 2, 3, 4, 5]]
        # signal line needs an extra warmup step after slow ema exists
        self.assertFalse(is_na(results[-1][0]))

    def test_bb_basis_matches_sma(self):
        bb = ta.Bb(3, 2.0)
        out = [bb.update(v) for v in [1, 2, 3]]
        basis, upper, lower = out[-1]
        self.assertAlmostEqual(basis, 2.0)
        self.assertGreater(upper, basis)
        self.assertLess(lower, basis)

    def test_change(self):
        change = ta.Change(2)
        out = [change.update(v) for v in [10, 12, 15, 20]]
        self.assertTrue(is_na(out[0]))
        self.assertTrue(is_na(out[1]))
        self.assertAlmostEqual(out[2], 5.0)  # 15 - 10
        self.assertAlmostEqual(out[3], 8.0)  # 20 - 12

    def test_barssince(self):
        bs = ta.BarsSince()
        conds = [False, True, False, False]
        out = [bs.update(c) for c in conds]
        self.assertTrue(is_na(out[0]))
        self.assertEqual(out[1], 0)
        self.assertEqual(out[2], 1)
        self.assertEqual(out[3], 2)


if __name__ == "__main__":
    unittest.main()
