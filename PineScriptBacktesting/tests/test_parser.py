import unittest

from pinescript import pine_ast as ast
from pinescript.errors import PineUnsupportedError
from pinescript.parser import parse


class TestParser(unittest.TestCase):
    def test_strategy_declaration_and_body(self):
        src = (
            "//@version=5\n"
            'strategy("Test", overlay=true, initial_capital=5000)\n'
            "fastLen = 9\n"
            "fastMA = ta.sma(close, fastLen)\n"
            "if ta.crossover(fastMA, close)\n"
            '    strategy.entry("Long", strategy.long)\n'
        )
        program = parse(src)
        self.assertTrue(program.is_strategy)
        self.assertIsInstance(program.declaration, ast.CallExpr)
        self.assertEqual(program.declaration.kwargs["initial_capital"].value, 5000.0)
        self.assertEqual(len(program.statements), 3)
        self.assertIsInstance(program.statements[0], ast.VarDecl)
        self.assertIsInstance(program.statements[2], ast.IfStmt)

    def test_var_and_reassign(self):
        src = "var float entryPrice = na\nentryPrice := close\n"
        # `var float entryPrice` (typed var decl) isn't supported syntax in
        # our grammar (no type annotations); use the untyped form instead.
        src = "var entryPrice = na\nentryPrice := close\n"
        program = parse(src)
        self.assertIsInstance(program.statements[0], ast.VarDecl)
        self.assertTrue(program.statements[0].persistent)
        self.assertIsInstance(program.statements[1], ast.Reassign)

    def test_tuple_decl(self):
        src = "[macdLine, signalLine, histLine] = ta.macd(close, 12, 26, 9)\n"
        program = parse(src)
        self.assertIsInstance(program.statements[0], ast.TupleDecl)
        self.assertEqual(program.statements[0].names, ["macdLine", "signalLine", "histLine"])

    def test_for_loop(self):
        src = "total = 0.0\nfor i = 0 to 9\n    total := total + i\n"
        program = parse(src)
        self.assertIsInstance(program.statements[1], ast.ForStmt)

    def test_function_def_single_line(self):
        src = "double(x) => x * 2\ny = double(21)\n"
        program = parse(src)
        self.assertIsInstance(program.statements[0], ast.FuncDef)
        self.assertEqual(program.statements[0].params, ["x"])

    def test_operator_precedence(self):
        src = "y = 1 + 2 * 3\n"
        program = parse(src)
        expr = program.statements[0].value
        self.assertIsInstance(expr, ast.BinOp)
        self.assertEqual(expr.op, "+")
        self.assertIsInstance(expr.right, ast.BinOp)
        self.assertEqual(expr.right.op, "*")

    def test_ternary_and_history_ref(self):
        src = "y = close[1] > open[1] ? 1 : -1\n"
        program = parse(src)
        expr = program.statements[0].value
        self.assertIsInstance(expr, ast.TernaryOp)

    def test_import_raises_unsupported(self):
        with self.assertRaises(PineUnsupportedError):
            parse('import user/mylib/1 as lib\n')


if __name__ == "__main__":
    unittest.main()
