"""Bar-by-bar tree-walking interpreter for the parsed Pine AST.

Design notes
------------
- Every top-level variable is treated as a "series": `self.history[name]`
  holds its finalized value for each bar already processed, and
  `self.current[name]` holds the value assigned so far *this* bar. Plain
  `x = expr` recomputes `current[name]` every bar; `var x = expr` only runs
  its initializer once (bar 0) and otherwise keeps last bar's value until
  explicitly changed with `x := expr`. This intentionally uses a single
  flat namespace rather than Pine's real block scoping - see the README
  "Known limitations" section.
- `ta.*` calls are call-site persistent (see indicators.py): each AST call
  node gets its own indicator-state instance the first time it's reached,
  then is fed one value per bar for the rest of the run.
- `strategy.*` order calls are forwarded to a caller-supplied broker
  object (see backtest/broker.py) which owns all fill simulation; this
  module only handles the Pine language semantics.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import indicators as ta
from . import pine_ast as ast
from .errors import PineRuntimeError, PineUnsupportedError
from .na import NA, is_na, to_number

BUILTIN_ARRAYS = {"open", "high", "low", "close", "volume", "time", "hl2", "hlc3", "ohlc4"}

NOOP_CALLS = {
    "plot", "plotshape", "plotchar", "plotarrow", "plotcandle", "plotbar",
    "bgcolor", "fill", "hline", "alertcondition", "alert",
    "label.new", "label.delete", "label.set_x", "label.set_y", "label.set_text",
    "line.new", "line.delete", "table.new", "table.cell", "box.new",
    "runtime.error", "log.info", "log.warning", "log.error",
}


def truthy(value: Any) -> bool:
    if is_na(value):
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return bool(value)


class _Return(Exception):
    def __init__(self, value: Any):
        self.value = value


class _Break(Exception):
    pass


class _Continue(Exception):
    pass


class Frame:
    __slots__ = ("locals",)

    def __init__(self, locals_: Dict[str, Any]):
        self.locals = locals_


class Interpreter:
    def __init__(self, program: ast.Program, bars: List[dict], broker: Optional[Any] = None):
        self.program = program
        self.bars = bars
        self.broker = broker

        self.arrays: Dict[str, List[float]] = {
            "open": [b["open"] for b in bars],
            "high": [b["high"] for b in bars],
            "low": [b["low"] for b in bars],
            "close": [b["close"] for b in bars],
            "volume": [b.get("volume", 0.0) for b in bars],
            "time": [b.get("time", i) for i, b in enumerate(bars)],
        }
        self.arrays["hl2"] = [(h + l) / 2.0 for h, l in zip(self.arrays["high"], self.arrays["low"])]
        self.arrays["hlc3"] = [
            (h + l + c) / 3.0
            for h, l, c in zip(self.arrays["high"], self.arrays["low"], self.arrays["close"])
        ]
        self.arrays["ohlc4"] = [
            (o + h + l + c) / 4.0
            for o, h, l, c in zip(
                self.arrays["open"], self.arrays["high"], self.arrays["low"], self.arrays["close"]
            )
        ]

        self.functions: Dict[str, ast.FuncDef] = {
            stmt.name: stmt for stmt in program.statements if isinstance(stmt, ast.FuncDef)
        }

        self.history: Dict[str, List[Any]] = {}
        self.current: Dict[str, Any] = {}
        self.initialized: set = set()
        self.indicator_states: Dict[int, Any] = {}
        self.call_output_history: Dict[int, List[Any]] = {}
        self.call_stack: List[Frame] = []
        self.bar_idx: int = 0

    # ---- declaration (static) evaluation ----------------------------------

    def eval_declaration_kwargs(self) -> Dict[str, Any]:
        decl = self.program.declaration
        result: Dict[str, Any] = {}
        if decl is None:
            return result
        if decl.args:
            result["title"] = self._eval(decl.args[0])
        for key, value_node in decl.kwargs.items():
            result[key] = self._eval(value_node)
        return result

    # ---- bar loop -----------------------------------------------------------

    def run_bar(self, bar_idx: int) -> None:
        self.bar_idx = bar_idx
        self._exec_block(self.program.statements)
        self._finalize_bar()

    def _finalize_bar(self) -> None:
        for name, value in self.current.items():
            self.history.setdefault(name, []).append(value)

    # ---- statement execution -------------------------------------------------

    def _exec_block(self, stmts: List[ast.Node]) -> None:
        for stmt in stmts:
            self._exec_stmt(stmt)

    def _exec_stmt(self, node: ast.Node) -> None:
        if isinstance(node, ast.VarDecl):
            self._exec_var_decl(node)
        elif isinstance(node, ast.TupleDecl):
            value = self._eval_rhs(node.value)
            if not isinstance(value, (tuple, list)):
                raise PineRuntimeError(f"Expected a tuple result at line {node.line}")
            for name, v in zip(node.names, value):
                self.current[name] = v
        elif isinstance(node, ast.Reassign):
            self.current[node.name] = self._eval_rhs(node.value)
        elif isinstance(node, ast.ExprStmt):
            self._eval(node.expr)
        elif isinstance(node, ast.IfStmt):
            if truthy(self._eval(node.cond)):
                self._exec_block(node.then_body)
            elif node.else_body is not None:
                self._exec_block(node.else_body)
        elif isinstance(node, ast.ForStmt):
            self._exec_for(node)
        elif isinstance(node, ast.FuncDef):
            pass  # registered up-front in __init__
        elif isinstance(node, ast.ReturnStmt):
            raise _Return(self._eval(node.value) if node.value is not None else NA)
        elif isinstance(node, ast.BreakStmt):
            raise _Break()
        elif isinstance(node, ast.ContinueStmt):
            raise _Continue()
        else:
            raise PineRuntimeError(f"Unhandled statement node {type(node).__name__}")

    def _exec_var_decl(self, node: ast.VarDecl) -> None:
        if node.persistent:
            if node.name not in self.initialized:
                self.current[node.name] = self._eval_rhs(node.value)
                self.initialized.add(node.name)
            # else: value already carried forward in self.current
        else:
            self.current[node.name] = self._eval_rhs(node.value)

    def _exec_for(self, node: ast.ForStmt) -> None:
        start = int(to_number(self._eval(node.start)))
        end = int(to_number(self._eval(node.end)))
        step = int(to_number(self._eval(node.step))) if node.step is not None else (1 if end >= start else -1)
        if step == 0:
            step = 1
        i = start
        while (step > 0 and i <= end) or (step < 0 and i >= end):
            self.current[node.var_name] = float(i)
            try:
                self._exec_block(node.body)
            except _Continue:
                pass
            except _Break:
                break
            i += step

    def _eval_rhs(self, node: ast.Node) -> Any:
        """Evaluate the right-hand side of an assignment, which in Pine may
        itself be a multi-line if-expression."""
        if isinstance(node, ast.IfStmt):
            return self._eval_if_expr(node)
        return self._eval(node)

    def _eval_if_expr(self, node: ast.IfStmt) -> Any:
        branch = node.then_body if truthy(self._eval(node.cond)) else (node.else_body or [])
        result: Any = NA
        for i, stmt in enumerate(branch):
            is_last = i == len(branch) - 1
            if is_last and isinstance(stmt, ast.ExprStmt):
                result = self._eval(stmt.expr)
            elif is_last and isinstance(stmt, ast.IfStmt):
                result = self._eval_if_expr(stmt)
            elif is_last and isinstance(stmt, (ast.VarDecl, ast.TupleDecl, ast.Reassign)):
                self._exec_stmt(stmt)
                result = self.current.get(getattr(stmt, "name", None), NA)
            else:
                self._exec_stmt(stmt)
        return result

    # ---- expression evaluation ------------------------------------------------

    def _eval(self, node: ast.Node) -> Any:
        if isinstance(node, ast.NumberLit):
            return node.value
        if isinstance(node, ast.StringLit):
            return node.value
        if isinstance(node, ast.BoolLit):
            return node.value
        if isinstance(node, ast.NaLit):
            return NA
        if isinstance(node, ast.Identifier):
            return self._eval_identifier(node.name, 0)
        if isinstance(node, ast.MemberExpr):
            return self._eval_member(node)
        if isinstance(node, ast.HistoryRef):
            return self._eval_history_ref(node)
        if isinstance(node, ast.UnaryOp):
            return self._eval_unary(node)
        if isinstance(node, ast.BinOp):
            return self._eval_binop(node)
        if isinstance(node, ast.TernaryOp):
            cond = self._eval(node.cond)
            return self._eval(node.if_true) if truthy(cond) else self._eval(node.if_false)
        if isinstance(node, ast.CallExpr):
            return self._eval_call(node)
        if isinstance(node, ast.IfStmt):
            return self._eval_if_expr(node)
        raise PineRuntimeError(f"Cannot evaluate node {type(node).__name__}")

    def _eval_identifier(self, name: str, offset: int) -> Any:
        if self.call_stack and name in self.call_stack[-1].locals:
            return self.call_stack[-1].locals[name]
        if name == "bar_index":
            idx = self.bar_idx - offset
            return float(idx) if idx >= 0 else NA
        if name in BUILTIN_ARRAYS:
            arr = self.arrays[name]
            idx = self.bar_idx - offset
            return arr[idx] if 0 <= idx < len(arr) else NA
        if offset == 0:
            return self.current.get(name, NA)
        hist = self.history.get(name)
        if not hist:
            return NA
        idx = len(hist) - offset
        return hist[idx] if 0 <= idx < len(hist) else NA

    def _eval_history_ref(self, node: ast.HistoryRef) -> Any:
        offset = int(to_number(self._eval(node.index)))
        if isinstance(node.target, ast.Identifier):
            return self._eval_identifier(node.target.name, offset)
        if isinstance(node.target, ast.MemberExpr):
            # e.g. `strategy.position_size[1]`: rare; only current value supported.
            return self._eval_member(node.target)
        if isinstance(node.target, ast.CallExpr):
            key = id(node.target)
            self._eval_call(node.target)  # ensure this bar's value is recorded
            hist = self.call_output_history.get(key, [])
            idx = len(hist) - 1 - offset
            return hist[idx] if 0 <= idx < len(hist) else NA
        raise PineUnsupportedError(
            f"History reference `[]` on this expression is not supported (line {node.line})"
        )

    def _eval_unary(self, node: ast.UnaryOp) -> Any:
        value = self._eval(node.operand)
        if node.op == "-":
            return NA if is_na(value) else -to_number(value)
        if node.op == "not":
            return not truthy(value)
        raise PineRuntimeError(f"Unknown unary operator {node.op}")

    def _eval_binop(self, node: ast.BinOp) -> Any:
        if node.op == "and":
            left = self._eval(node.left)
            if not truthy(left):
                return False
            return truthy(self._eval(node.right))
        if node.op == "or":
            left = self._eval(node.left)
            if truthy(left):
                return True
            return truthy(self._eval(node.right))

        left = self._eval(node.left)
        right = self._eval(node.right)

        if node.op in ("+", "-", "*", "/", "%") and (
            isinstance(left, str) or isinstance(right, str)
        ):
            if node.op == "+":
                return f"{left}{right}"
            raise PineRuntimeError(f"Invalid string operation '{node.op}' at line {node.line}")

        if node.op in ("==", "!="):
            if is_na(left) or is_na(right):
                result = is_na(left) and is_na(right)
            else:
                result = left == right
            return result if node.op == "==" else not result

        ln, rn = to_number(left), to_number(right)
        if ln != ln or rn != rn:  # na propagation for ordered comparisons/arith
            if node.op in ("<", ">", "<=", ">="):
                return False
            return NA

        if node.op == "+":
            return ln + rn
        if node.op == "-":
            return ln - rn
        if node.op == "*":
            return ln * rn
        if node.op == "/":
            return NA if rn == 0 else ln / rn
        if node.op == "%":
            return NA if rn == 0 else ln % rn
        if node.op == "<":
            return ln < rn
        if node.op == ">":
            return ln > rn
        if node.op == "<=":
            return ln <= rn
        if node.op == ">=":
            return ln >= rn
        raise PineRuntimeError(f"Unknown binary operator {node.op}")

    # ---- calls -----------------------------------------------------------------

    def _eval_call(self, node: ast.CallExpr) -> Any:
        result = self._dispatch_call(node)
        key = id(node)
        hist = self.call_output_history.setdefault(key, [])
        if len(hist) <= self.bar_idx:
            hist.append(result)
        else:
            hist[self.bar_idx] = result
        return result

    def _dispatch_call(self, node: ast.CallExpr) -> Any:
        callee = node.callee
        if isinstance(callee, ast.Identifier):
            return self._call_by_name(callee.name, node)
        if isinstance(callee, ast.MemberExpr):
            return self._call_member(callee.path, node)
        raise PineRuntimeError(f"Cannot call non-function expression at line {node.line}")

    def _call_by_name(self, name: str, node: ast.CallExpr) -> Any:
        if name in self.functions:
            return self._call_user_function(self.functions[name], node)
        if name == "na":
            return is_na(self._eval(node.args[0]))
        if name == "nz":
            value = self._eval(node.args[0])
            if len(node.args) > 1:
                replacement = self._eval(node.args[1])
            elif "y" in node.kwargs:
                replacement = self._eval(node.kwargs["y"])
            else:
                replacement = 0.0
            return replacement if is_na(value) else value
        if name == "int":
            value = self._eval(node.args[0])
            return NA if is_na(value) else float(int(to_number(value)))
        if name == "float":
            value = self._eval(node.args[0])
            return NA if is_na(value) else to_number(value)
        if name == "bool":
            return truthy(self._eval(node.args[0]))
        if name == "abs":
            value = to_number(self._eval(node.args[0]))
            return NA if value != value else abs(value)
        if name in ("min", "max"):
            values = [to_number(self._eval(a)) for a in node.args]
            return (min if name == "min" else max)(values)
        if name in NOOP_CALLS:
            for a in node.args:
                self._eval(a)
            for v in node.kwargs.values():
                self._eval(v)
            return NA
        if name in ("strategy", "indicator", "study"):
            return NA
        raise PineUnsupportedError(f"Unknown/unsupported function '{name}' at line {node.line}")

    def _call_user_function(self, func: ast.FuncDef, node: ast.CallExpr) -> Any:
        args = [self._eval(a) for a in node.args]
        locals_: Dict[str, Any] = dict(zip(func.params, args))
        for key, value_node in node.kwargs.items():
            locals_[key] = self._eval(value_node)
        self.call_stack.append(Frame(locals_))
        try:
            result: Any = NA
            body = func.body
            for i, stmt in enumerate(body):
                try:
                    if i == len(body) - 1 and isinstance(stmt, ast.ExprStmt):
                        result = self._eval(stmt.expr)
                    else:
                        self._exec_stmt(stmt)
                except _Return as ret:
                    result = ret.value
                    break
            return result
        finally:
            self.call_stack.pop()

    def _get_indicator_state(self, node: ast.CallExpr, factory):
        key = id(node)
        state = self.indicator_states.get(key)
        if state is None:
            state = factory()
            self.indicator_states[key] = state
        return state

    def _call_member(self, path: List[str], node: ast.CallExpr) -> Any:
        dotted = ".".join(path)
        args = node.args
        kwargs = node.kwargs

        def arg(i: Optional[int], key: Optional[str] = None, default: Any = None) -> Any:
            if i is not None and i < len(args):
                return self._eval(args[i])
            if key is not None and key in kwargs:
                return self._eval(kwargs[key])
            return default

        if path[0] == "ta":
            return self._call_ta(path[1], node, arg)
        if path[0] == "math":
            return self._call_math(path[1], arg)
        if path[0] == "strategy":
            return self._call_strategy(path[1], node, arg)
        if path[0] == "input":
            # input.int/input.float/... : no UI, just returns the default.
            return arg(0, "defval", 0.0)
        if dotted in NOOP_CALLS or path[0] in ("label", "line", "table", "box", "log", "runtime"):
            for a in args:
                self._eval(a)
            for v in kwargs.values():
                self._eval(v)
            return NA
        raise PineUnsupportedError(f"Unsupported call '{dotted}(...)' at line {node.line}")

    def _call_ta(self, fn: str, node: ast.CallExpr, arg) -> Any:
        if fn == "sma":
            state = self._get_indicator_state(node, lambda: ta.Sma(arg(1)))
            return state.update(to_number(arg(0)))
        if fn == "ema":
            state = self._get_indicator_state(node, lambda: ta.Ema(arg(1)))
            return state.update(to_number(arg(0)))
        if fn == "rma":
            state = self._get_indicator_state(node, lambda: ta.Rma(arg(1)))
            return state.update(to_number(arg(0)))
        if fn == "wma":
            state = self._get_indicator_state(node, lambda: ta.Wma(arg(1)))
            return state.update(to_number(arg(0)))
        if fn == "vwma":
            state = self._get_indicator_state(node, lambda: ta.Vwma(arg(1)))
            return state.update(to_number(arg(0)), to_number(arg(1, "volume", self.arrays["volume"][self.bar_idx])))
        if fn == "stdev":
            state = self._get_indicator_state(node, lambda: ta.Stdev(arg(1), bool(arg(2, "biased", True))))
            return state.update(to_number(arg(0)))
        if fn == "variance":
            state = self._get_indicator_state(node, lambda: ta.Stdev(arg(1), bool(arg(2, "biased", True))))
            v = state.update(to_number(arg(0)))
            return NA if is_na(v) else v * v
        if fn == "highest":
            state = self._get_indicator_state(node, lambda: ta.Highest(arg(1)))
            return state.update(to_number(arg(0)))
        if fn == "lowest":
            state = self._get_indicator_state(node, lambda: ta.Lowest(arg(1)))
            return state.update(to_number(arg(0)))
        if fn == "highestbars":
            state = self._get_indicator_state(node, lambda: ta.HighestBars(arg(1)))
            return state.update(to_number(arg(0)))
        if fn == "lowestbars":
            state = self._get_indicator_state(node, lambda: ta.LowestBars(arg(1)))
            return state.update(to_number(arg(0)))
        if fn == "rsi":
            state = self._get_indicator_state(node, lambda: ta.Rsi(arg(1)))
            return state.update(to_number(arg(0)))
        if fn == "atr":
            state = self._get_indicator_state(node, lambda: ta.Atr(arg(0)))
            return state.update(
                self.arrays["high"][self.bar_idx],
                self.arrays["low"][self.bar_idx],
                self.arrays["close"][self.bar_idx],
            )
        if fn == "tr":
            state = self._get_indicator_state(node, lambda: ta.Atr(1))
            return state.update(
                self.arrays["high"][self.bar_idx],
                self.arrays["low"][self.bar_idx],
                self.arrays["close"][self.bar_idx],
            )
        if fn == "stoch":
            state = self._get_indicator_state(node, lambda: ta.Stoch(arg(3)))
            return state.update(to_number(arg(0)), to_number(arg(1)), to_number(arg(2)))
        if fn == "macd":
            state = self._get_indicator_state(
                node, lambda: ta.Macd(arg(1), arg(2), arg(3))
            )
            return state.update(to_number(arg(0)))
        if fn in ("bb", "bbands"):
            state = self._get_indicator_state(node, lambda: ta.Bb(arg(1), arg(2, "mult", 2.0)))
            return state.update(to_number(arg(0)))
        if fn == "crossover":
            state = self._get_indicator_state(node, ta.Cross)
            return state.crossover(to_number(arg(0)), to_number(arg(1)))
        if fn == "crossunder":
            state = self._get_indicator_state(node, ta.Cross)
            return state.crossunder(to_number(arg(0)), to_number(arg(1)))
        if fn == "cross":
            state = self._get_indicator_state(node, ta.Cross)
            return state.cross(to_number(arg(0)), to_number(arg(1)))
        if fn == "change":
            state = self._get_indicator_state(node, lambda: ta.Change(arg(1, default=1)))
            return state.update(to_number(arg(0)))
        if fn == "cum":
            state = self._get_indicator_state(node, ta.Cum)
            return state.update(to_number(arg(0)))
        if fn == "valuewhen":
            state = self._get_indicator_state(node, lambda: ta.ValueWhen(arg(2, default=0)))
            return state.update(truthy(arg(0)), to_number(arg(1)))
        if fn == "barssince":
            state = self._get_indicator_state(node, ta.BarsSince)
            return state.update(truthy(arg(0)))
        raise PineUnsupportedError(f"ta.{fn}(...) is not supported")

    def _call_math(self, fn: str, arg) -> Any:
        import math as pymath

        if fn == "abs":
            v = to_number(arg(0))
            return NA if v != v else abs(v)
        if fn == "max":
            return max(to_number(arg(0)), to_number(arg(1)))
        if fn == "min":
            return min(to_number(arg(0)), to_number(arg(1)))
        if fn == "round":
            v = to_number(arg(0))
            return NA if v != v else float(round(v))
        if fn == "floor":
            v = to_number(arg(0))
            return NA if v != v else float(pymath.floor(v))
        if fn == "ceil":
            v = to_number(arg(0))
            return NA if v != v else float(pymath.ceil(v))
        if fn == "pow":
            return to_number(arg(0)) ** to_number(arg(1))
        if fn == "sqrt":
            v = to_number(arg(0))
            return NA if v < 0 else pymath.sqrt(v)
        if fn == "log":
            v = to_number(arg(0))
            return NA if v <= 0 else pymath.log(v)
        if fn == "log10":
            v = to_number(arg(0))
            return NA if v <= 0 else pymath.log10(v)
        if fn == "sign":
            v = to_number(arg(0))
            return 0.0 if v == 0 else (1.0 if v > 0 else -1.0)
        if fn == "avg":
            return sum(to_number(arg(i)) for i in range(2)) / 2.0
        raise PineUnsupportedError(f"math.{fn}(...) is not supported")

    def _call_strategy(self, fn: str, node: ast.CallExpr, arg) -> Any:
        if self.broker is None:
            raise PineRuntimeError("strategy.* call requires a broker to be attached")
        if fn == "entry":
            self.broker.entry(
                id=arg(0),
                direction=self._normalize_direction(arg(1)),
                qty=arg(2, "qty"),
                limit=arg(None, "limit"),
                stop=arg(None, "stop"),
                comment=arg(None, "comment"),
            )
            return NA
        if fn == "order":
            self.broker.order(
                id=arg(0),
                direction=self._normalize_direction(arg(1)),
                qty=arg(2, "qty"),
                limit=arg(None, "limit"),
                stop=arg(None, "stop"),
                comment=arg(None, "comment"),
            )
            return NA
        if fn == "exit":
            self.broker.exit(
                id=arg(0),
                from_entry=arg(1, "from_entry"),
                qty=arg(None, "qty"),
                qty_percent=arg(None, "qty_percent"),
                profit=arg(None, "profit"),
                loss=arg(None, "loss"),
                limit=arg(None, "limit"),
                stop=arg(None, "stop"),
                trail_points=arg(None, "trail_points"),
                trail_offset=arg(None, "trail_offset"),
                comment=arg(None, "comment"),
            )
            return NA
        if fn == "close":
            self.broker.close(
                id=arg(0), comment=arg(None, "comment"), qty_percent=arg(None, "qty_percent")
            )
            return NA
        if fn == "close_all":
            self.broker.close_all(comment=arg(0, "comment"))
            return NA
        if fn == "cancel":
            self.broker.cancel(arg(0))
            return NA
        if fn == "cancel_all":
            self.broker.cancel_all()
            return NA
        raise PineUnsupportedError(f"strategy.{fn}(...) is not supported")

    @staticmethod
    def _normalize_direction(value: Any) -> str:
        if value in ("long", "short"):
            return value
        if value in (1, 1.0):
            return "long"
        if value in (-1, -1.0):
            return "short"
        return "long"

    # ---- member (non-call) access ------------------------------------------------

    def _eval_member(self, node: ast.MemberExpr) -> Any:
        path = node.path
        head = path[0]
        if head == "strategy":
            return self._eval_strategy_member(path[1:])
        if head == "syminfo":
            return self._eval_syminfo_member(path[1:])
        if head in ("ta", "math", "input", "color", "line", "label", "table"):
            # Constant-like sub-namespace access used only as strategy()/
            # indicator() config values, e.g. `strategy.commission.percent`
            # handled below; anything else falls back to its dotted name.
            return ".".join(path[1:]) if len(path) > 1 else head
        raise PineUnsupportedError(f"Unsupported identifier '{'.'.join(path)}' at line {node.line}")

    def _eval_strategy_member(self, rest: List[str]) -> Any:
        name = ".".join(rest)
        constants = {
            "long": "long",
            "short": "short",
            "cash": "cash",
            "fixed": "fixed",
            "percent_of_equity": "percent_of_equity",
            "commission.percent": "percent",
            "commission.cash_per_contract": "cash_per_contract",
            "commission.cash_per_order": "cash_per_order",
            "direction.long": "long",
            "direction.short": "short",
            "direction.all": "all",
            "oca.cancel": "cancel",
            "oca.reduce": "reduce",
            "oca.none": "none",
        }
        if name in constants:
            return constants[name]
        if self.broker is not None and hasattr(self.broker, name.replace(".", "_")):
            return getattr(self.broker, name.replace(".", "_"))
        return name

    def _eval_syminfo_member(self, rest: List[str]) -> Any:
        name = ".".join(rest)
        if name == "mintick":
            return 0.01
        if name == "pointvalue":
            return 1.0
        return NA
