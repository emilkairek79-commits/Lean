"""Tokenizer for the supported Pine Script (v5/v6) subset.

Pine uses Python-like significant indentation for blocks (if/for/function
bodies), so the lexer tracks indentation the same way CPython's tokenizer
does: an INDENT/DEDENT token is synthesized whenever a logical line's
leading whitespace grows or shrinks relative to the enclosing block.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .errors import PineSyntaxError

KEYWORDS = {
    "if", "else", "for", "to", "by", "while", "var", "varip",
    "and", "or", "not", "true", "false", "na", "import", "export",
    "type", "switch", "continue", "break", "return",
}

# Multi-char operators must be tried before their single-char prefixes.
SYMBOLS = [
    "=>", ":=", "==", "!=", "<=", ">=", "?", ":", "+", "-", "*", "/", "%",
    "(", ")", "[", "]", ",", ".", "<", ">", "=", "\n",
]


@dataclass
class Token:
    type: str
    value: object
    line: int
    col: int

    def __repr__(self) -> str:
        return f"Token({self.type!r}, {self.value!r}, L{self.line})"


class Lexer:
    def __init__(self, source: str):
        # Normalize line endings and strip a version annotation / comments.
        self.source = source.replace("\r\n", "\n").replace("\r", "\n")
        self.tokens: List[Token] = []

    def tokenize(self) -> List[Token]:
        lines = self.source.split("\n")
        indent_stack = [0]
        pending: List[Token] = []
        paren_depth = 0

        for lineno, raw_line in enumerate(lines, start=1):
            line = self._strip_comment(raw_line)
            if line.strip() == "":
                continue

            indent = len(line) - len(line.lstrip(" "))
            content = line[indent:]

            if paren_depth == 0:
                if indent > indent_stack[-1]:
                    indent_stack.append(indent)
                    pending.append(Token("INDENT", indent, lineno, 0))
                while indent < indent_stack[-1]:
                    indent_stack.pop()
                    pending.append(Token("DEDENT", indent, lineno, 0))
                if indent not in indent_stack:
                    # Re-sync to nearest known indent to tolerate odd spacing.
                    indent_stack.append(indent)

            col = indent
            i = 0
            n = len(content)
            while i < n:
                ch = content[i]
                if ch == " " or ch == "\t":
                    i += 1
                    continue
                if ch.isdigit() or (ch == "." and i + 1 < n and content[i + 1].isdigit()):
                    j = i
                    while j < n and (content[j].isdigit() or content[j] == "."):
                        j += 1
                    pending.append(Token("NUMBER", float(content[i:j]), lineno, col))
                    i = j
                    continue
                if ch in ("'", '"'):
                    quote = ch
                    j = i + 1
                    while j < n and content[j] != quote:
                        j += 1
                    pending.append(Token("STRING", content[i + 1:j], lineno, col))
                    i = j + 1
                    continue
                if ch.isalpha() or ch == "_":
                    j = i
                    while j < n and (content[j].isalnum() or content[j] == "_"):
                        j += 1
                    word = content[i:j]
                    if word in ("true", "false"):
                        pending.append(Token("BOOL", word == "true", lineno, col))
                    elif word == "na":
                        pending.append(Token("NA", None, lineno, col))
                    elif word in KEYWORDS:
                        pending.append(Token(word.upper(), word, lineno, col))
                    else:
                        pending.append(Token("IDENT", word, lineno, col))
                    i = j
                    continue
                matched = False
                for sym in SYMBOLS:
                    if content.startswith(sym, i):
                        if sym in ("(", "["):
                            paren_depth += 1
                        elif sym in (")", "]"):
                            paren_depth = max(0, paren_depth - 1)
                        pending.append(Token(sym, sym, lineno, col))
                        i += len(sym)
                        matched = True
                        break
                if matched:
                    continue
                raise PineSyntaxError(f"Unexpected character {ch!r} at line {lineno}")
            if paren_depth == 0:
                pending.append(Token("NEWLINE", "\n", lineno, col))

        while len(indent_stack) > 1:
            indent_stack.pop()
            pending.append(Token("DEDENT", 0, len(lines) + 1, 0))
        pending.append(Token("EOF", None, len(lines) + 1, 0))
        self.tokens = pending
        return self.tokens

    @staticmethod
    def _strip_comment(line: str) -> str:
        in_string = None
        for idx, ch in enumerate(line):
            if in_string:
                if ch == in_string:
                    in_string = None
                continue
            if ch in ("'", '"'):
                in_string = ch
            elif ch == "/" and idx + 1 < len(line) and line[idx + 1] == "/":
                return line[:idx]
        return line
