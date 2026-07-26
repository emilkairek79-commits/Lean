import unittest

from pinescript.interpreter import Interpreter
from pinescript.na import is_na
from pinescript.parser import parse


def make_bars(closes):
    bars = []
    for c in closes:
        bars.append({"open": c, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": 100.0})
    return bars


def run(source, closes):
    program = parse(source)
    bars = make_bars(closes)
    interp = Interpreter(program, bars)
    for i in range(len(bars)):
        interp.run_bar(i)
    return interp


class TestInterpreter(unittest.TestCase):
    def test_plain_var_recomputes_every_bar(self):
        interp = run("x = close * 2\n", [1, 2, 3])
        self.assertEqual(interp.history["x"], [2.0, 4.0, 6.0])

    def test_var_keyword_persists_until_reassigned(self):
        src = "var total = 0.0\ntotal := total + close\n"
        interp = run(src, [1, 2, 3])
        self.assertEqual(interp.history["total"], [1.0, 3.0, 6.0])

    def test_var_without_reassign_carries_forward(self):
        src = 'var flag = true\nif close > 100\n    flag := false\n'
        interp = run(src, [1, 2, 3])
        self.assertEqual(interp.history["flag"], [True, True, True])

    def test_history_ref(self):
        interp = run("y = close[1]\n", [10, 20, 30])
        self.assertTrue(is_na(interp.history["y"][0]))
        self.assertEqual(interp.history["y"][1], 10.0)
        self.assertEqual(interp.history["y"][2], 20.0)

    def test_if_expression_assignment(self):
        src = "y = if close > 2\n    100\nelse\n    -100\n"
        interp = run(src, [1, 2, 3])
        self.assertEqual(interp.history["y"], [-100.0, -100.0, 100.0])

    def test_for_loop_accumulates(self):
        src = "total = 0.0\nfor i = 1 to 4\n    total := total + i\n"
        interp = run(src, [1])
        self.assertEqual(interp.history["total"], [10.0])  # 1+2+3+4

    def test_user_function_call(self):
        src = "double(x) => x * 2\ny = double(close)\n"
        interp = run(src, [1, 2, 3])
        self.assertEqual(interp.history["y"], [2.0, 4.0, 6.0])

    def test_tuple_destructure_from_macd(self):
        src = "[macdLine, signalLine, histLine] = ta.macd(close, 2, 4, 2)\n"
        interp = run(src, [1, 2, 3, 4, 5, 6, 7, 8])
        self.assertIn("macdLine", interp.history)
        self.assertIn("histLine", interp.history)

    def test_na_and_nz(self):
        src = "y = na\nz = nz(y, 42)\n"
        interp = run(src, [1])
        self.assertTrue(is_na(interp.history["y"][0]))
        self.assertEqual(interp.history["z"][0], 42.0)

    def test_ternary(self):
        interp = run("y = close > 1 ? 100 : -100\n", [0, 5])
        self.assertEqual(interp.history["y"], [-100.0, 100.0])

    def test_sma_via_ta_namespace(self):
        interp = run("y = ta.sma(close, 2)\n", [2, 4, 6])
        self.assertTrue(is_na(interp.history["y"][0]))
        self.assertAlmostEqual(interp.history["y"][1], 3.0)
        self.assertAlmostEqual(interp.history["y"][2], 5.0)


if __name__ == "__main__":
    unittest.main()
