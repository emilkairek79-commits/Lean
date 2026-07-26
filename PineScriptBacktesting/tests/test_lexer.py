import unittest

from pinescript.lexer import Lexer


class TestLexer(unittest.TestCase):
    def test_indentation_tokens(self):
        src = "if true\n    x = 1\ny = 2\n"
        tokens = Lexer(src).tokenize()
        types = [t.type for t in tokens]
        self.assertIn("INDENT", types)
        self.assertIn("DEDENT", types)
        self.assertEqual(types[-1], "EOF")

    def test_numbers_and_operators(self):
        tokens = Lexer("x = 1.5 + 2\n").tokenize()
        values = [(t.type, t.value) for t in tokens if t.type != "NEWLINE"]
        self.assertEqual(
            values,
            [("IDENT", "x"), ("=", "="), ("NUMBER", 1.5), ("+", "+"), ("NUMBER", 2.0), ("EOF", None)],
        )

    def test_comment_stripped(self):
        tokens = Lexer("x = 1 // comment\n").tokenize()
        types = [t.type for t in tokens]
        self.assertNotIn("STRING", types)
        self.assertEqual([t.value for t in tokens if t.type == "NUMBER"], [1.0])

    def test_parens_suppress_newline(self):
        src = "strategy(\n    \"t\",\n    overlay=true\n)\n"
        tokens = Lexer(src).tokenize()
        newline_count = sum(1 for t in tokens if t.type == "NEWLINE")
        self.assertEqual(newline_count, 1)

    def test_string_and_bool_literals(self):
        tokens = Lexer('x = "hi"\ny = true\n').tokenize()
        kinds = {(t.type, t.value) for t in tokens}
        self.assertIn(("STRING", "hi"), kinds)
        self.assertIn(("BOOL", True), kinds)

    def test_history_ref_brackets(self):
        tokens = Lexer("y = close[1]\n").tokenize()
        types = [t.type for t in tokens if t.type != "NEWLINE"]
        self.assertIn("[", types)
        self.assertIn("]", types)


if __name__ == "__main__":
    unittest.main()
