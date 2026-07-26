"""Order-fill simulation that mirrors TradingView's non-tick broker emulator.

Per-bar order of operations (matches TradingView with the default
`calc_on_every_tick=false`, `process_orders_on_close=false` and no bar
magnifier, which is the only mode this engine supports - see the README
"Known limitations"):

  1. Fill orders queued by the *previous* bar's script run, using this
     bar's OHLC (market orders fill at the open; limit/stop orders fill
     only if the bar's range touches their price).
  2. Check any active take-profit/stop-loss/trailing-stop brackets
     against this bar's range.
  3. Report position/equity state to the script for this bar.
  4. Run the script for this bar; any strategy.entry/exit/close/order
     calls it makes are queued for step 1 of the *next* bar.

Slippage is always 0 and gap fills use the bar's open - real tick-level
fills are unavailable, so this is a bar-based approximation, not a
tick-perfect replica.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .results import BacktestResult, EquityPoint, Trade


@dataclass
class _PendingOrder:
    kind: str  # 'entry' | 'close' | 'close_all' | 'order'
    id: str
    direction: Optional[str] = None
    qty: Optional[float] = None
    order_type: str = "market"  # 'market' | 'limit' | 'stop'
    price: Optional[float] = None
    qty_percent: Optional[float] = None
    comment: Optional[str] = None


@dataclass
class _Bracket:
    from_entry: Optional[str]
    qty: Optional[float] = None
    qty_percent: Optional[float] = None
    profit_price: Optional[float] = None
    loss_price: Optional[float] = None
    trail_points: Optional[float] = None
    trail_offset: Optional[float] = None
    trail_active: bool = False
    trail_stop_price: Optional[float] = None
    high_water: Optional[float] = None
    comment: Optional[str] = None


class Broker:
    def __init__(
        self,
        initial_capital: float = 10000.0,
        default_qty_type: str = "percent_of_equity",
        default_qty_value: float = 100.0,
        commission_type: Optional[str] = None,
        commission_value: float = 0.0,
        pyramiding: int = 1,
    ):
        self.initial_capital = initial_capital
        self.default_qty_type = default_qty_type
        self.default_qty_value = default_qty_value
        self.commission_type = commission_type
        self.commission_value = commission_value
        self.pyramiding = max(1, int(pyramiding))

        self.cash = initial_capital
        self.position_direction: Optional[str] = None
        self.position_qty: float = 0.0
        self.position_avg_price: float = 0.0
        self.position_entries: int = 0  # count of merged entries (pyramiding)
        self.current_trade: Optional[Trade] = None
        self.entry_id_of_current: Optional[str] = None

        self._pending: List[_PendingOrder] = []
        self._brackets: Dict[str, _Bracket] = {}

        self.result = BacktestResult(initial_capital=initial_capital)

        # readable by interpreter as strategy.* members
        self.position_size = 0.0
        self.equity = initial_capital
        self.open_profit = 0.0
        self.net_profit = 0.0
        self.opentrades = 0
        self.closedtrades = 0

    # ---- called by Interpreter (strategy.* calls) ------------------------------

    def entry(self, id, direction, qty=None, limit=None, stop=None, comment=None):
        order_type = "limit" if limit is not None else ("stop" if stop is not None else "market")
        price = limit if limit is not None else stop
        self._pending.append(
            _PendingOrder(
                kind="entry", id=id, direction=direction, qty=qty,
                order_type=order_type, price=price, comment=comment,
            )
        )

    def order(self, id, direction, qty, limit=None, stop=None, comment=None):
        self.entry(id, direction, qty=qty, limit=limit, stop=stop, comment=comment)

    def exit(
        self, id, from_entry=None, qty=None, qty_percent=None, profit=None, loss=None,
        limit=None, stop=None, trail_points=None, trail_offset=None, comment=None,
    ):
        if self.position_qty == 0:
            return
        ref_price = self.position_avg_price
        sign = 1 if self.position_direction == "long" else -1
        profit_price = limit if limit is not None else (
            ref_price + sign * profit if profit is not None else None
        )
        loss_price = stop if stop is not None else (
            ref_price - sign * loss if loss is not None else None
        )
        existing = self._brackets.get(id)
        self._brackets[id] = _Bracket(
            from_entry=from_entry, qty=qty, qty_percent=qty_percent,
            profit_price=profit_price, loss_price=loss_price,
            trail_points=trail_points, trail_offset=trail_offset,
            trail_active=existing.trail_active if existing else False,
            trail_stop_price=existing.trail_stop_price if existing else None,
            high_water=existing.high_water if existing else None,
            comment=comment,
        )

    def close(self, id=None, comment=None, qty_percent=None):
        if self.position_qty == 0:
            return
        self._pending.append(
            _PendingOrder(kind="close", id=id or "close", qty_percent=qty_percent, comment=comment)
        )

    def close_all(self, comment=None):
        if self.position_qty == 0:
            return
        self._pending.append(_PendingOrder(kind="close_all", id="close_all", comment=comment))

    def cancel(self, id):
        self._pending = [o for o in self._pending if o.id != id]
        self._brackets.pop(id, None)

    def cancel_all(self):
        self._pending.clear()
        self._brackets.clear()

    # ---- called by engine, once per bar -----------------------------------------

    def process_bar(self, bar_idx: int, bar_time: Any, o: float, h: float, l: float, c: float) -> None:
        self._fill_pending(bar_idx, bar_time, o, h, l)
        self._check_brackets(bar_idx, bar_time, o, h, l)
        self._update_readable_state(c)

    def _update_readable_state(self, mark_price: float) -> None:
        self.position_size = self.position_qty * (1 if self.position_direction == "long" else -1)
        if self.position_qty > 0:
            sign = 1 if self.position_direction == "long" else -1
            self.open_profit = (mark_price - self.position_avg_price) * self.position_qty * sign
        else:
            self.open_profit = 0.0
        self.equity = self.cash + self.open_profit
        self.net_profit = self.equity - self.initial_capital
        self.opentrades = 1 if self.position_qty > 0 else 0
        self.closedtrades = sum(1 for t in self.result.trades if t.is_closed)

    def record_equity_point(self, bar_idx: int, bar_time: Any) -> None:
        self.result.equity_curve.append(
            EquityPoint(bar=bar_idx, time=bar_time, equity=self.equity, open_profit=self.open_profit)
        )

    # ---- internals ---------------------------------------------------------------

    def _commission(self, qty: float, price: float) -> float:
        if not self.commission_type or not self.commission_value:
            return 0.0
        if self.commission_type == "percent":
            return qty * price * (self.commission_value / 100.0)
        if self.commission_type == "cash_per_contract":
            return qty * self.commission_value
        if self.commission_type == "cash_per_order":
            return self.commission_value
        return 0.0

    def _size_order(self, price: float) -> float:
        if self.default_qty_type == "fixed":
            return self.default_qty_value
        if self.default_qty_type == "cash":
            return self.default_qty_value / price if price else 0.0
        # percent_of_equity
        return (self.equity * self.default_qty_value / 100.0) / price if price else 0.0

    def _fill_pending(self, bar_idx: int, bar_time: Any, o: float, h: float, l: float) -> None:
        if not self._pending:
            return
        remaining: List[_PendingOrder] = []
        for order in self._pending:
            fill_price = self._resolve_fill_price(order, o, h, l)
            if fill_price is None:
                remaining.append(order)  # limit/stop not yet triggered
                continue
            if order.kind == "entry":
                self._apply_entry(order, bar_idx, bar_time, fill_price)
            elif order.kind == "close":
                self._apply_close(bar_idx, bar_time, fill_price, order.qty_percent, order.comment)
            elif order.kind == "close_all":
                self._apply_close(bar_idx, bar_time, fill_price, None, order.comment)
        self._pending = remaining

    @staticmethod
    def _resolve_fill_price(order: _PendingOrder, o: float, h: float, l: float) -> Optional[float]:
        if order.kind in ("close", "close_all") or order.order_type == "market":
            return o
        if order.order_type == "limit":
            is_buy = order.direction == "long"
            if is_buy:
                return order.price if l <= order.price else None
            return order.price if h >= order.price else None
        if order.order_type == "stop":
            is_buy = order.direction == "long"
            if is_buy:
                return order.price if h >= order.price else None
            return order.price if l <= order.price else None
        return o

    def _apply_entry(self, order: _PendingOrder, bar_idx: int, bar_time: Any, fill_price: float) -> None:
        qty = order.qty if order.qty is not None else self._size_order(fill_price)
        if qty <= 0:
            return
        commission = self._commission(qty, fill_price)

        if self.position_qty == 0:
            self._open_position(order, bar_idx, bar_time, fill_price, qty, commission)
            return

        if order.direction == self.position_direction:
            if self.position_entries >= self.pyramiding:
                return  # pyramiding cap reached, order rejected
            total_qty = self.position_qty + qty
            self.position_avg_price = (
                self.position_avg_price * self.position_qty + fill_price * qty
            ) / total_qty
            self.position_qty = total_qty
            self.position_entries += 1
            self.cash -= commission
            if self.current_trade is not None:
                self.current_trade.qty = self.position_qty
                self.current_trade.entry_price = self.position_avg_price
                self.current_trade.entry_commission += commission
            return

        # Opposite direction: flatten current position, then open the remainder.
        self._close_current_trade(bar_idx, bar_time, fill_price, comment="reverse")
        self._open_position(order, bar_idx, bar_time, fill_price, qty, commission)

    def _open_position(self, order: _PendingOrder, bar_idx, bar_time, fill_price, qty, commission) -> None:
        self.position_direction = order.direction
        self.position_qty = qty
        self.position_avg_price = fill_price
        self.position_entries = 1
        self.entry_id_of_current = order.id
        self.cash -= commission
        self.current_trade = Trade(
            entry_id=order.id, direction=order.direction, qty=qty,
            entry_bar=bar_idx, entry_time=bar_time, entry_price=fill_price,
            entry_commission=commission,
        )
        self.result.trades.append(self.current_trade)
        self._brackets.clear()

    def _apply_close(self, bar_idx, bar_time, fill_price, qty_percent, comment) -> None:
        if qty_percent is not None and qty_percent < 100 and self.current_trade is not None:
            self._close_partial(bar_idx, bar_time, fill_price, qty_percent, comment)
        else:
            self._close_current_trade(bar_idx, bar_time, fill_price, comment)

    def _close_partial(self, bar_idx, bar_time, fill_price, qty_percent, comment) -> None:
        trade = self.current_trade
        close_qty = self.position_qty * (qty_percent / 100.0)
        sign = 1 if self.position_direction == "long" else -1
        commission = self._commission(close_qty, fill_price)
        pnl = (fill_price - trade.entry_price) * close_qty * sign - commission
        self.cash += pnl
        trade.pnl = (trade.pnl or 0.0) + pnl
        trade.exit_commission += commission
        remaining_qty = self.position_qty - close_qty
        self.position_qty = remaining_qty
        if remaining_qty <= 1e-9:
            trade.exit_bar = bar_idx
            trade.exit_time = bar_time
            trade.exit_price = fill_price
            trade.exit_comment = comment
            self.position_direction = None
            self.position_qty = 0.0
            self.position_avg_price = 0.0
            self.position_entries = 0
            self.current_trade = None
            self._brackets.clear()
        else:
            trade.qty = remaining_qty

    def _close_current_trade(self, bar_idx, bar_time, fill_price, comment=None) -> None:
        if self.current_trade is None or self.position_qty == 0:
            return
        trade = self.current_trade
        sign = 1 if self.position_direction == "long" else -1
        commission = self._commission(self.position_qty, fill_price)
        pnl = (fill_price - trade.entry_price) * self.position_qty * sign - commission
        self.cash += pnl
        trade.exit_bar = bar_idx
        trade.exit_time = bar_time
        trade.exit_price = fill_price
        trade.exit_commission = commission
        trade.pnl = pnl
        trade.exit_comment = comment
        self.position_direction = None
        self.position_qty = 0.0
        self.position_avg_price = 0.0
        self.position_entries = 0
        self.current_trade = None
        self._brackets.clear()

    def _check_brackets(self, bar_idx: int, bar_time: Any, o: float, h: float, l: float) -> None:
        if self.position_qty == 0 or not self._brackets:
            return
        sign = 1 if self.position_direction == "long" else -1

        for bracket_id, b in list(self._brackets.items()):
            if b.trail_points is not None and b.trail_offset is not None:
                self._update_trailing(b, sign, self.position_avg_price, h, l)

            profit_hit = b.profit_price is not None and (
                (sign == 1 and h >= b.profit_price) or (sign == -1 and l <= b.profit_price)
            )
            loss_price = b.trail_stop_price if b.trail_active else b.loss_price
            loss_hit = loss_price is not None and (
                (sign == 1 and l <= loss_price) or (sign == -1 and h >= loss_price)
            )

            if not profit_hit and not loss_hit:
                continue

            if profit_hit and loss_hit:
                # Both touched in the same bar: without tick data we can't
                # know which hit first. Approximate with whichever price is
                # closer to the bar's open (see README limitations).
                if abs(o - b.profit_price) <= abs(o - loss_price):
                    profit_hit, loss_hit = True, False
                else:
                    profit_hit, loss_hit = False, True

            if profit_hit:
                target = b.profit_price
                fill = o if (sign == 1 and o > target) or (sign == -1 and o < target) else target
            else:
                target = loss_price
                fill = o if (sign == 1 and o < target) or (sign == -1 and o > target) else target

            self._apply_close(bar_idx, bar_time, fill, None, b.comment or bracket_id)
            break  # position now flat; remaining brackets cleared already

    @staticmethod
    def _update_trailing(b: _Bracket, sign: int, entry_price: float, h: float, l: float) -> None:
        extreme = h if sign == 1 else l
        if b.high_water is None:
            b.high_water = extreme
        else:
            b.high_water = max(b.high_water, extreme) if sign == 1 else min(b.high_water, extreme)
        if not b.trail_active:
            favorable_move = (b.high_water - entry_price) if sign == 1 else (entry_price - b.high_water)
            if favorable_move >= b.trail_points:
                b.trail_active = True
        if b.trail_active:
            b.trail_stop_price = (
                b.high_water - b.trail_offset if sign == 1 else b.high_water + b.trail_offset
            )
