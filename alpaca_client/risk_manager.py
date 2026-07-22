"""Hard risk limits for order submission.

These are enforced checks, not suggestions: place_order() in TradingClient
consults RiskManager.validate_order() before submitting anything to Alpaca,
and refuses to submit if it returns allowed=False.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from .journal import TradeJournal


@dataclass
class RiskDecision:
    allowed: bool
    reason: str


class RiskManager:
    def __init__(
        self,
        max_risk_per_trade_pct: float = 0.01,
        daily_max_drawdown_pct: float = 0.05,
        journal: Optional[TradeJournal] = None,
    ):
        """
        max_risk_per_trade_pct: fraction of equity a single order's notional
            value may not exceed (default 1%). Typical range: 0.01-0.02.
        daily_max_drawdown_pct: fraction equity may drop from the start of
            the trading day before new orders are blocked (default 5%).
        """
        if not 0 < max_risk_per_trade_pct <= 1:
            raise ValueError("max_risk_per_trade_pct must be between 0 and 1")
        if not 0 < daily_max_drawdown_pct <= 1:
            raise ValueError("daily_max_drawdown_pct must be between 0 and 1")

        self.max_risk_per_trade_pct = max_risk_per_trade_pct
        self.daily_max_drawdown_pct = daily_max_drawdown_pct
        self.journal = journal

        self._trading_day: Optional[date] = None
        self._day_start_equity: Optional[float] = None
        self._manually_halted = False
        self._drawdown_halted_today = False

    @property
    def is_halted(self) -> bool:
        return self._manually_halted or self._drawdown_halted_today

    def halt_trading(self) -> None:
        """Manually block all new orders until resume_trading() is called."""
        self._manually_halted = True

    def resume_trading(self) -> None:
        self._manually_halted = False
        self._drawdown_halted_today = False

    def _sync_trading_day(self, equity: float) -> None:
        today = date.today()
        if self._trading_day != today:
            self._trading_day = today
            self._day_start_equity = equity
            self._drawdown_halted_today = False

    def _drawdown_breached(self, equity: float) -> bool:
        if not self._day_start_equity:
            return False
        drawdown_pct = (self._day_start_equity - equity) / self._day_start_equity
        return drawdown_pct >= self.daily_max_drawdown_pct

    def validate_order(
        self,
        *,
        symbol: str,
        qty: float,
        side: str,
        equity: float,
        estimated_price: float,
        entry_reason: Optional[str] = None,
    ) -> RiskDecision:
        self._sync_trading_day(equity)

        if self._manually_halted:
            decision = RiskDecision(False, "Trading manually halted")
        elif self._drawdown_breached(equity):
            self._drawdown_halted_today = True
            decision = RiskDecision(
                False,
                f"Daily drawdown limit reached ({self.daily_max_drawdown_pct:.1%}); "
                "new orders blocked for the rest of the day",
            )
        else:
            notional = qty * estimated_price
            max_notional = equity * self.max_risk_per_trade_pct
            if notional > max_notional:
                decision = RiskDecision(
                    False,
                    f"Order notional ${notional:,.2f} exceeds max risk per trade "
                    f"(${max_notional:,.2f} = {self.max_risk_per_trade_pct:.1%} of equity ${equity:,.2f})",
                )
            else:
                decision = RiskDecision(True, "within risk limits")

        if self.journal is not None:
            self.journal.log_decision(
                symbol=symbol,
                qty=qty,
                side=side,
                allowed=decision.allowed,
                reason=decision.reason,
                entry_reason=entry_reason,
            )

        return decision
