"""AST node types produced by the parser and walked by the interpreter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


class Node:
    line: int = 0


@dataclass
class Program(Node):
    declaration: Optional["CallExpr"]  # the strategy(...)/indicator(...) call
    is_strategy: bool
    statements: List[Node]


# ---- Expressions -----------------------------------------------------

@dataclass
class NumberLit(Node):
    value: float
    line: int = 0


@dataclass
class StringLit(Node):
    value: str
    line: int = 0


@dataclass
class BoolLit(Node):
    value: bool
    line: int = 0


@dataclass
class NaLit(Node):
    line: int = 0


@dataclass
class Identifier(Node):
    name: str
    line: int = 0


@dataclass
class MemberExpr(Node):
    """e.g. `ta.sma`, `strategy.position_size`."""
    path: List[str]
    line: int = 0


@dataclass
class HistoryRef(Node):
    """`expr[n]` - historical reference, n bars back."""
    target: Node
    index: Node
    line: int = 0


@dataclass
class UnaryOp(Node):
    op: str
    operand: Node
    line: int = 0


@dataclass
class BinOp(Node):
    op: str
    left: Node
    right: Node
    line: int = 0


@dataclass
class TernaryOp(Node):
    cond: Node
    if_true: Node
    if_false: Node
    line: int = 0


@dataclass
class CallExpr(Node):
    callee: Node  # Identifier or MemberExpr
    args: List[Node]
    kwargs: dict
    line: int = 0


# ---- Statements --------------------------------------------------------

@dataclass
class VarDecl(Node):
    name: str
    value: Node
    persistent: bool  # `var`/`varip`
    line: int = 0


@dataclass
class TupleDecl(Node):
    """`[a, b, c] = ta.macd(...)` tuple destructuring."""
    names: List[str]
    value: Node
    line: int = 0


@dataclass
class Reassign(Node):
    """`name := expr`"""
    name: str
    value: Node
    line: int = 0


@dataclass
class ExprStmt(Node):
    expr: Node
    line: int = 0


@dataclass
class IfStmt(Node):
    cond: Node
    then_body: List[Node]
    else_body: Optional[List[Node]]
    result_var: Optional[str] = None  # set when if-expr result is assigned
    line: int = 0


@dataclass
class ForStmt(Node):
    var_name: str
    start: Node
    end: Node
    step: Optional[Node]
    body: List[Node]
    line: int = 0


@dataclass
class FuncDef(Node):
    name: str
    params: List[str]
    body: List[Node]
    line: int = 0


@dataclass
class ReturnStmt(Node):
    value: Optional[Node]
    line: int = 0


@dataclass
class BreakStmt(Node):
    line: int = 0


@dataclass
class ContinueStmt(Node):
    line: int = 0
