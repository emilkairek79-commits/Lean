"""Streaming implementations of Pine Script's `ta.*` built-ins.

Pine's `ta.*` functions are call-site persistent: `ta.sma(close, 14)`
written once in a script keeps its own internal state across bars, fed
one new value per bar, rather than recomputing over an explicit array
each time. We mirror that: the interpreter creates one instance of the
matching class below per *call site* (keyed by the AST node's id) and
calls `.update(...)` with this bar's inputs, once per bar, in order.

Formulas follow the documented Pine behaviour:
- ta.sma: simple average, na until `length` samples collected.
- ta.ema: alpha = 2/(length+1), seeded with the first sample (no warmup).
- ta.rma (Wilder smoothing, used by rsi/atr): seeded with an sma of the
  first `length` samples, then `(prev*(length-1)+src)/length`.
- ta.wma: linearly weighted average, weight `i` (1..length) on the i-th
  oldest sample in the window.
- ta.stdev: population standard deviation (Pine's default `biased=true`).
"""

from __future__ import annotations

from collections import deque
from typing import Optional, Tuple

from .na import NA, is_na


class Sma:
    def __init__(self, length: float):
        self.length = max(1, int(length))
        self.window: deque = deque(maxlen=self.length)

    def update(self, src: float):
        self.window.append(src)
        if len(self.window) < self.length or any(is_na(v) for v in self.window):
            return NA
        return sum(self.window) / self.length


class Ema:
    def __init__(self, length: float):
        self.length = max(1, int(length))
        self.alpha = 2.0 / (self.length + 1.0)
        self.prev: Optional[float] = None

    def update(self, src: float):
        if is_na(src):
            return NA
        if self.prev is None:
            self.prev = src
        else:
            self.prev = self.alpha * src + (1 - self.alpha) * self.prev
        return self.prev


class Rma:
    """Wilder's moving average, used internally by ta.rsi/ta.atr."""

    def __init__(self, length: float):
        self.length = max(1, int(length))
        self.window: deque = deque(maxlen=self.length)
        self.prev: Optional[float] = None

    def update(self, src: float):
        if is_na(src):
            return NA
        if self.prev is None:
            self.window.append(src)
            if len(self.window) < self.length:
                return NA
            self.prev = sum(self.window) / self.length
            return self.prev
        self.prev = (self.prev * (self.length - 1) + src) / self.length
        return self.prev


class Wma:
    def __init__(self, length: float):
        self.length = max(1, int(length))
        self.window: deque = deque(maxlen=self.length)

    def update(self, src: float):
        self.window.append(src)
        n = self.length
        if len(self.window) < n or any(is_na(v) for v in self.window):
            return NA
        weights = range(1, n + 1)
        numerator = sum(w * v for w, v in zip(weights, self.window))
        denominator = n * (n + 1) / 2
        return numerator / denominator


class Vwma:
    def __init__(self, length: float):
        self.num = Sma(length)
        self.den = Sma(length)

    def update(self, src: float, volume: float):
        num = self.num.update(src * volume)
        den = self.den.update(volume)
        if is_na(num) or is_na(den) or den == 0:
            return NA
        return num / den


class Stdev:
    def __init__(self, length: float, biased: bool = True):
        self.length = max(1, int(length))
        self.window: deque = deque(maxlen=self.length)
        self.biased = biased

    def update(self, src: float):
        self.window.append(src)
        if len(self.window) < self.length or any(is_na(v) for v in self.window):
            return NA
        mean = sum(self.window) / self.length
        denom = self.length if self.biased else max(1, self.length - 1)
        variance = sum((v - mean) ** 2 for v in self.window) / denom
        return variance ** 0.5


class Highest:
    def __init__(self, length: float):
        self.length = max(1, int(length))
        self.window: deque = deque(maxlen=self.length)

    def update(self, src: float):
        self.window.append(src)
        if len(self.window) < self.length or any(is_na(v) for v in self.window):
            return NA
        return max(self.window)


class Lowest:
    def __init__(self, length: float):
        self.length = max(1, int(length))
        self.window: deque = deque(maxlen=self.length)

    def update(self, src: float):
        self.window.append(src)
        if len(self.window) < self.length or any(is_na(v) for v in self.window):
            return NA
        return min(self.window)


class HighestBars:
    def __init__(self, length: float):
        self.length = max(1, int(length))
        self.window: deque = deque(maxlen=self.length)

    def update(self, src: float):
        self.window.append(src)
        if len(self.window) < self.length or any(is_na(v) for v in self.window):
            return NA
        vals = list(self.window)
        idx = max(range(len(vals)), key=lambda i: vals[i])
        return -(len(vals) - 1 - idx)


class LowestBars:
    def __init__(self, length: float):
        self.length = max(1, int(length))
        self.window: deque = deque(maxlen=self.length)

    def update(self, src: float):
        self.window.append(src)
        if len(self.window) < self.length or any(is_na(v) for v in self.window):
            return NA
        vals = list(self.window)
        idx = min(range(len(vals)), key=lambda i: vals[i])
        return -(len(vals) - 1 - idx)


class Rsi:
    def __init__(self, length: float):
        self.up = Rma(length)
        self.down = Rma(length)
        self.prev_src: Optional[float] = None

    def update(self, src: float):
        if is_na(src):
            return NA
        change = 0.0 if self.prev_src is None else src - self.prev_src
        self.prev_src = src
        gain = max(change, 0.0)
        loss = -min(change, 0.0)
        avg_gain = self.up.update(gain)
        avg_loss = self.down.update(loss)
        if is_na(avg_gain) or is_na(avg_loss):
            return NA
        if avg_loss == 0:
            return 100.0
        if avg_gain == 0:
            return 0.0
        rs = avg_gain / avg_loss
        return 100.0 - 100.0 / (1.0 + rs)


class Atr:
    def __init__(self, length: float):
        self.rma = Rma(length)
        self.prev_close: Optional[float] = None

    def update(self, high: float, low: float, close: float):
        if self.prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - self.prev_close), abs(low - self.prev_close))
        self.prev_close = close
        return self.rma.update(tr)


class Stoch:
    def __init__(self, length: float):
        self.highest = Highest(length)
        self.lowest = Lowest(length)

    def update(self, src: float, high: float, low: float):
        hh = self.highest.update(high)
        ll = self.lowest.update(low)
        if is_na(hh) or is_na(ll) or hh == ll:
            return NA
        return 100.0 * (src - ll) / (hh - ll)


class Macd:
    def __init__(self, fast_length: float, slow_length: float, signal_length: float):
        self.fast = Ema(fast_length)
        self.slow = Ema(slow_length)
        self.signal = Ema(signal_length)

    def update(self, src: float) -> Tuple[object, object, object]:
        fast = self.fast.update(src)
        slow = self.slow.update(src)
        if is_na(fast) or is_na(slow):
            return NA, NA, NA
        macd_line = fast - slow
        signal_line = self.signal.update(macd_line)
        hist = NA if is_na(signal_line) else macd_line - signal_line
        return macd_line, signal_line, hist


class Bb:
    def __init__(self, length: float, mult: float):
        self.basis = Sma(length)
        self.dev = Stdev(length, biased=True)
        self.mult = mult

    def update(self, src: float) -> Tuple[object, object, object]:
        basis = self.basis.update(src)
        dev = self.dev.update(src)
        if is_na(basis) or is_na(dev):
            return NA, NA, NA
        offset = dev * self.mult
        return basis, basis + offset, basis - offset


class Cross:
    def __init__(self):
        self.prev_a: Optional[float] = None
        self.prev_b: Optional[float] = None

    def _step(self, a: float, b: float):
        prev_a, prev_b = self.prev_a, self.prev_b
        self.prev_a, self.prev_b = a, b
        return prev_a, prev_b

    def crossover(self, a: float, b: float) -> bool:
        prev_a, prev_b = self._step(a, b)
        if prev_a is None or is_na(a) or is_na(b) or is_na(prev_a) or is_na(prev_b):
            return False
        return prev_a <= prev_b and a > b

    def crossunder(self, a: float, b: float) -> bool:
        prev_a, prev_b = self._step(a, b)
        if prev_a is None or is_na(a) or is_na(b) or is_na(prev_a) or is_na(prev_b):
            return False
        return prev_a >= prev_b and a < b

    def cross(self, a: float, b: float) -> bool:
        prev_a, prev_b = self._step(a, b)
        if prev_a is None or is_na(a) or is_na(b) or is_na(prev_a) or is_na(prev_b):
            return False
        return (prev_a - prev_b) * (a - b) < 0


class Change:
    def __init__(self, length: float = 1):
        self.length = max(1, int(length))
        self.window: deque = deque(maxlen=self.length + 1)

    def update(self, src: float):
        self.window.append(src)
        if len(self.window) <= self.length:
            return NA
        return self.window[-1] - self.window[0]


class Cum:
    def __init__(self):
        self.total = 0.0

    def update(self, src: float):
        if is_na(src):
            return self.total
        self.total += src
        return self.total


class ValueWhen:
    def __init__(self, occurrence: float = 0):
        self.occurrence = max(0, int(occurrence))
        self.history: deque = deque(maxlen=self.occurrence + 1)

    def update(self, condition: bool, source: float):
        if condition:
            self.history.appendleft(source)
        if len(self.history) <= self.occurrence:
            return NA
        return self.history[self.occurrence]


class BarsSince:
    def __init__(self):
        self.count: Optional[int] = None

    def update(self, condition: bool):
        if condition:
            self.count = 0
            return 0
        if self.count is None:
            return NA
        self.count += 1
        return self.count
