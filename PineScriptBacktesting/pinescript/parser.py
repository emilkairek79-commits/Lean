"""Recursive-descent parser for the supported Pine Script subset.

Grammar covers: `//@version=`, `strategy()`/`indicator()` declarations,
`var`/`varip` declarations, `:=` reassignment, `if/else` (as statement or
expression), `for` loops, single-line and indented function definitions,
member access (`ta.sma`), history refs (`x[1]`), and the usual operators
with Pine's precedence (ternary, or, and, not, comparisons, +-, */%,
unary, postfix `[]`/call/`.`).

Not supported (raises PineUnsupportedError): `import`, `type` (UDTs),
`switch`, `while`, arrays/matrices/maps, `request.security`.
"""

from __future__ import annotations

from typing import List, Optional

from . import pine_ast as ast
from .errors import PineSyntaxError, PineUnsupportedError
from .lexer import Lexer, Token


class Parser:
    def __init__(self, source: str):
        # Strip the `//@version=5` pragma line if present; it carries no
        # semantic weight for this engine beyond documenting intent.
        self.tokens = Lexer(source).tokenize()
        self.pos = 0

    # -- token stream helpers ------------------------------------------------

    def _peek(self, offset: int = 0) -> Token:
        idx = min(self.pos + offset, len(self.tokens) - 1)
        return self.tokens[idx]

    def _advance(self) -> Token:
        tok = self.tokens[self.pos]
        if tok.type != "EOF":
            self.pos += 1
        return tok

    def _check(self, *types: str) -> bool:
        return self._peek().type in types

    def _accept(self, *types: str) -> Optional[Token]:
        if self._check(*types):
            return self._advance()
        return None

    def _expect(self, type_: str) -> Token:
        tok = self._peek()
        if tok.type != type_:
            raise PineSyntaxError(
                f"Expected {type_} but got {tok.type} ({tok.value!r}) at line {tok.line}"
            )
        return self._advance()

    def _skip_newlines(self) -> None:
        while self._check("NEWLINE"):
            self._advance()

    # -- top level ------------------------------------------------------------

    def parse_program(self) -> ast.Program:
        self._skip_newlines()
        declaration = None
        is_strategy = True
        statements: List[ast.Node] = []

        while not self._check("EOF"):
            self._skip_newlines()
            if self._check("EOF"):
                break
            if self._check("IMPORT"):
                raise PineUnsupportedError(
                    "`import` of custom/community libraries is not supported "
                    "by this engine (line %d)" % self._peek().line
                )
            if self._check("TYPE"):
                raise PineUnsupportedError(
                    "User-defined types (`type ...`) are not supported (line %d)"
                    % self._peek().line
                )
            stmt = self._parse_statement()
            if (
                declaration is None
                and isinstance(stmt, ast.ExprStmt)
                and isinstance(stmt.expr, ast.CallExpr)
                and isinstance(stmt.expr.callee, ast.Identifier)
                and stmt.expr.callee.name in ("strategy", "indicator", "study")
            ):
                declaration = stmt.expr
                is_strategy = stmt.expr.callee.name == "strategy"
                continue
            statements.append(stmt)
            self._skip_newlines()

        return ast.Program(declaration=declaration, is_strategy=is_strategy, statements=statements)

    # -- statements -------------------------------------------------------------

    def _parse_block(self) -> List[ast.Node]:
        self._expect("INDENT")
        body: List[ast.Node] = []
        self._skip_newlines()
        while not self._check("DEDENT", "EOF"):
            body.append(self._parse_statement())
            self._skip_newlines()
        self._accept("DEDENT")
        return body

    def _parse_statement(self) -> ast.Node:
        if self._check("VAR", "VARIP"):
            return self._parse_var_decl()
        if self._check("IF"):
            return self._parse_if()
        if self._check("FOR"):
            return self._parse_for()
        if self._check("WHILE", "SWITCH"):
            raise PineUnsupportedError(
                f"`{self._peek().value}` loops/switch are not supported (line {self._peek().line})"
            )
        if self._check("RETURN"):
            kw = self._advance()
            value = None
            if not self._check("NEWLINE", "DEDENT", "EOF"):
                value = self._parse_expr()
            self._end_stmt()
            return ast.ReturnStmt(value=value, line=kw.line)
        if self._check("BREAK"):
            kw = self._advance()
            self._end_stmt()
            return ast.BreakStmt(line=kw.line)
        if self._check("CONTINUE"):
            kw = self._advance()
            self._end_stmt()
            return ast.ContinueStmt(line=kw.line)

        if self._check("[") and self._is_tuple_decl_lookahead():
            return self._parse_tuple_decl()

        # Lookahead for `name = expr`, `name := expr`, or `name(...) =>` (func def).
        if self._check("IDENT") and self._peek(1).type == ":=":
            name_tok = self._advance()
            self._advance()  # :=
            value = self._parse_expr()
            self._end_stmt()
            return ast.Reassign(name=name_tok.value, value=value, line=name_tok.line)

        if self._check("IDENT") and self._peek(1).type == "=" and self._peek(2).type != "=":
            name_tok = self._advance()
            self._advance()  # =
            value = self._parse_rhs()
            self._end_stmt()
            return ast.VarDecl(name=name_tok.value, value=value, persistent=False, line=name_tok.line)

        if self._is_func_def_lookahead():
            return self._parse_func_def()

        expr = self._parse_expr()
        self._end_stmt()
        return ast.ExprStmt(expr=expr, line=expr.line)

    def _end_stmt(self) -> None:
        if self._check("NEWLINE"):
            self._advance()
        elif not self._check("DEDENT", "EOF"):
            tok = self._peek()
            raise PineSyntaxError(f"Expected end of statement at line {tok.line}, got {tok.type}")

    def _is_tuple_decl_lookahead(self) -> bool:
        # `[` IDENT (, IDENT)* `]` `=`  (not followed by `==`)
        i = 1
        if self._peek(i).type != "IDENT":
            return False
        i += 1
        while self._peek(i).type == ",":
            i += 1
            if self._peek(i).type != "IDENT":
                return False
            i += 1
        return self._peek(i).type == "]" and self._peek(i + 1).type == "=" and self._peek(i + 2).type != "="

    def _parse_tuple_decl(self) -> ast.TupleDecl:
        open_tok = self._expect("[")
        names = [self._expect("IDENT").value]
        while self._accept(","):
            names.append(self._expect("IDENT").value)
        self._expect("]")
        self._expect("=")
        value = self._parse_expr()
        self._end_stmt()
        return ast.TupleDecl(names=names, value=value, line=open_tok.line)

    def _parse_var_decl(self) -> ast.VarDecl:
        kw = self._advance()  # var/varip
        name_tok = self._expect("IDENT")
        self._expect("=")
        value = self._parse_expr()
        self._end_stmt()
        return ast.VarDecl(name=name_tok.value, value=value, persistent=True, line=kw.line)

    def _is_func_def_lookahead(self) -> bool:
        if not self._check("IDENT"):
            return False
        if self._peek(1).type != "(":
            return False
        depth = 0
        i = 1
        while True:
            t = self._peek(i).type
            if t == "(":
                depth += 1
            elif t == ")":
                depth -= 1
                if depth == 0:
                    return self._peek(i + 1).type == "=>"
            elif t in ("EOF", "NEWLINE") and depth == 0:
                return False
            i += 1
            if i > 500:
                return False

    def _parse_func_def(self) -> ast.FuncDef:
        name_tok = self._advance()
        self._expect("(")
        params: List[str] = []
        while not self._check(")"):
            params.append(self._expect("IDENT").value)
            if not self._accept(","):
                break
        self._expect(")")
        self._expect("=>")
        if self._check("NEWLINE"):
            self._advance()
            body = self._parse_block()
        else:
            # single-line function body: `f(x) => x * 2`
            expr = self._parse_expr()
            self._end_stmt()
            body = [ast.ExprStmt(expr=expr, line=expr.line)]
        return ast.FuncDef(name=name_tok.value, params=params, body=body, line=name_tok.line)

    def _parse_if(self) -> ast.Node:
        kw = self._advance()
        cond = self._parse_expr()
        self._expect("NEWLINE")
        then_body = self._parse_block()
        else_body = None
        self._skip_blank_before_else()
        if self._check("ELSE"):
            self._advance()
            if self._check("IF"):
                else_body = [self._parse_if()]
            else:
                self._expect("NEWLINE")
                else_body = self._parse_block()
        return ast.IfStmt(cond=cond, then_body=then_body, else_body=else_body, line=kw.line)

    def _skip_blank_before_else(self) -> None:
        save = self.pos
        while self._check("NEWLINE"):
            self._advance()
        if not self._check("ELSE"):
            self.pos = save

    def _parse_for(self) -> ast.ForStmt:
        kw = self._advance()
        name_tok = self._expect("IDENT")
        self._expect("=")
        start = self._parse_expr()
        self._expect("TO")
        end = self._parse_expr()
        step = None
        if self._accept("BY"):
            step = self._parse_expr()
        self._expect("NEWLINE")
        body = self._parse_block()
        return ast.ForStmt(var_name=name_tok.value, start=start, end=end, step=step, body=body, line=kw.line)

    # -- expressions (precedence climbing) --------------------------------------

    def _parse_rhs(self) -> ast.Node:
        """RHS of `=`: may itself be a multi-line if-expression."""
        if self._check("IF"):
            return self._parse_if()
        return self._parse_expr()

    def _parse_expr(self) -> ast.Node:
        return self._parse_ternary()

    def _parse_ternary(self) -> ast.Node:
        cond = self._parse_or()
        if self._accept("?"):
            if_true = self._parse_ternary()
            self._expect(":")
            if_false = self._parse_ternary()
            return ast.TernaryOp(cond=cond, if_true=if_true, if_false=if_false, line=cond.line)
        return cond

    def _parse_or(self) -> ast.Node:
        left = self._parse_and()
        while self._check("OR"):
            op = self._advance()
            right = self._parse_and()
            left = ast.BinOp(op="or", left=left, right=right, line=op.line)
        return left

    def _parse_and(self) -> ast.Node:
        left = self._parse_not()
        while self._check("AND"):
            op = self._advance()
            right = self._parse_not()
            left = ast.BinOp(op="and", left=left, right=right, line=op.line)
        return left

    def _parse_not(self) -> ast.Node:
        if self._check("NOT"):
            op = self._advance()
            operand = self._parse_not()
            return ast.UnaryOp(op="not", operand=operand, line=op.line)
        return self._parse_comparison()

    def _parse_comparison(self) -> ast.Node:
        left = self._parse_additive()
        while self._check("==", "!=", "<", ">", "<=", ">="):
            op = self._advance()
            right = self._parse_additive()
            left = ast.BinOp(op=op.type, left=left, right=right, line=op.line)
        return left

    def _parse_additive(self) -> ast.Node:
        left = self._parse_multiplicative()
        while self._check("+", "-"):
            op = self._advance()
            right = self._parse_multiplicative()
            left = ast.BinOp(op=op.type, left=left, right=right, line=op.line)
        return left

    def _parse_multiplicative(self) -> ast.Node:
        left = self._parse_unary()
        while self._check("*", "/", "%"):
            op = self._advance()
            right = self._parse_unary()
            left = ast.BinOp(op=op.type, left=left, right=right, line=op.line)
        return left

    def _parse_unary(self) -> ast.Node:
        if self._check("-", "+"):
            op = self._advance()
            operand = self._parse_unary()
            if op.type == "-":
                return ast.UnaryOp(op="-", operand=operand, line=op.line)
            return operand
        return self._parse_postfix()

    def _parse_postfix(self) -> ast.Node:
        node = self._parse_primary()
        while True:
            if self._check("."):
                if isinstance(node, (ast.Identifier, ast.MemberExpr)):
                    self._advance()
                    attr = self._expect("IDENT")
                    path = (node.path if isinstance(node, ast.MemberExpr) else [node.name]) + [attr.value]
                    node = ast.MemberExpr(path=path, line=node.line)
                    continue
                break
            if self._check("("):
                node = self._parse_call(node)
                continue
            if self._check("["):
                self._advance()
                index = self._parse_expr()
                self._expect("]")
                node = ast.HistoryRef(target=node, index=index, line=node.line)
                continue
            break
        return node

    def _parse_call(self, callee: ast.Node) -> ast.CallExpr:
        line = self._expect("(").line
        args: List[ast.Node] = []
        kwargs = {}
        while not self._check(")"):
            if self._check("IDENT") and self._peek(1).type == "=" and self._peek(2).type != "=":
                key = self._advance().value
                self._advance()
                kwargs[key] = self._parse_expr()
            else:
                args.append(self._parse_expr())
            if not self._accept(","):
                break
        self._expect(")")
        return ast.CallExpr(callee=callee, args=args, kwargs=kwargs, line=line)

    def _parse_primary(self) -> ast.Node:
        tok = self._peek()
        if tok.type == "NUMBER":
            self._advance()
            return ast.NumberLit(value=tok.value, line=tok.line)
        if tok.type == "STRING":
            self._advance()
            return ast.StringLit(value=tok.value, line=tok.line)
        if tok.type == "BOOL":
            self._advance()
            return ast.BoolLit(value=tok.value, line=tok.line)
        if tok.type == "NA":
            self._advance()
            if self._check("("):
                # `na(x)` is the na-check function, distinct from the `na`
                # literal keyword; both share the token in Pine's grammar.
                return ast.Identifier(name="na", line=tok.line)
            return ast.NaLit(line=tok.line)
        if tok.type == "IDENT":
            self._advance()
            return ast.Identifier(name=tok.value, line=tok.line)
        if tok.type == "(":
            self._advance()
            expr = self._parse_expr()
            self._expect(")")
            return expr
        if tok.type == "IF":
            return self._parse_if()
        raise PineSyntaxError(f"Unexpected token {tok.type} ({tok.value!r}) at line {tok.line}")


def parse(source: str) -> ast.Program:
    return Parser(source).parse_program()
