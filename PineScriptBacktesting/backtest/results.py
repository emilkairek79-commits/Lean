"""Plain data containers for backtest output (no external dependencies)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class Trade:
    entry_id: str
    direction: str  # 'long' or 'short'
    qty: float
    entry_bar: int
    entry_time: Any
    entry_price: float
    entry_commission: float = 0.0
    exit_bar: Optional[int] = None
    exit_time: Any = None
    exit_price: Optional[float] = None
    exit_commission: float = 0.0
    exit_comment: Optional[str] = None
    pnl: Optional[float] = None  # realized, net of commission

    @property
    def is_closed(self) -> bool:
        return self.exit_price is not None

    @property
    def sign(self) -> int:
        return 1 if self.direction == "long" else -1


@dataclass
class EquityPoint:
    bar: int
    time: Any
    equity: float
    open_profit: float


@dataclass
class BacktestResult:
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[EquityPoint] = field(default_factory=list)
    initial_capital: float = 0.0
    metrics: dict = field(default_factory=dict)
