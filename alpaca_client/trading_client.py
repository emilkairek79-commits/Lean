"""TradingClient: a thin, risk-checked wrapper around alpaca-py for PAPER trading only.

No auto-execution logic lives here — every order is the result of an explicit
place_order() call made by the caller.
"""

from __future__ import annotations

from typing import Optional

from alpaca.trading.client import TradingClient as AlpacaTradingClient
from alpaca.trading.enums import OrderSide as AlpacaOrderSide
from alpaca.trading.enums import TimeInForce
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest

from .config import AlpacaConfig, load_config
from .exceptions import OrderValidationError, RiskLimitExceededError
from .journal import TradeJournal
from .risk_manager import RiskManager

VALID_SIDES = {"buy", "sell"}
VALID_ORDER_TYPES = {"market", "limit"}


class TradingClient:
    def __init__(
        self,
        config: Optional[AlpacaConfig] = None,
        risk_manager: Optional[RiskManager] = None,
        journal: Optional[TradeJournal] = None,
        dry_run: bool = False,
        trading_client=None,
        data_client=None,
    ):
        """
        trading_client / data_client: optional pre-built alpaca-py clients,
        primarily for dependency injection in tests. When omitted, real
        clients are built from environment-sourced config, always pointed
        at the paper trading endpoint.
        """
        self.dry_run = dry_run
        self.journal = journal or TradeJournal()
        self.risk_manager = risk_manager or RiskManager(journal=self.journal)

        if trading_client is not None:
            self._client = trading_client
            self._data_client = data_client
        else:
            self.config = config or load_config()
            self._client = AlpacaTradingClient(
                api_key=self.config.api_key,
                secret_key=self.config.api_secret,
                paper=True,
            )
            self._data_client = StockHistoricalDataClient(
                self.config.api_key, self.config.api_secret
            )

    def get_account_balance(self) -> dict:
        account = self._client.get_account()
        return {
            "equity": float(account.equity),
            "buying_power": float(account.buying_power),
            "cash": float(account.cash),
        }

    def get_positions(self) -> list[dict]:
        positions = self._client.get_all_positions()
        return [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "side": p.side.value if hasattr(p.side, "value") else str(p.side),
                "avg_entry_price": float(p.avg_entry_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
            }
            for p in positions
        ]

    def _estimate_price(self, symbol: str, limit_price: Optional[float]) -> float:
        if limit_price is not None:
            return float(limit_price)

        quote_map = self._data_client.get_stock_latest_quote(
            StockLatestQuoteRequest(symbol_or_symbols=symbol)
        )
        quote = quote_map[symbol]
        bid, ask = float(quote.bid_price or 0), float(quote.ask_price or 0)
        if bid and ask:
            return (bid + ask) / 2
        return ask or bid

    def place_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "market",
        limit_price: Optional[float] = None,
        entry_reason: Optional[str] = None,
    ):
        """Validate, risk-check, and (unless dry_run) submit a paper order.

        Raises OrderValidationError for malformed input and
        RiskLimitExceededError if the risk manager rejects the order.
        """
        side_l = side.lower()
        order_type_l = order_type.lower()

        if qty <= 0:
            raise OrderValidationError("qty must be positive")
        if side_l not in VALID_SIDES:
            raise OrderValidationError(f"side must be one of {VALID_SIDES}, got {side!r}")
        if order_type_l not in VALID_ORDER_TYPES:
            raise OrderValidationError(f"order_type must be one of {VALID_ORDER_TYPES}, got {order_type!r}")
        if order_type_l == "limit" and limit_price is None:
            raise OrderValidationError("limit_price is required for limit orders")

        equity = self.get_account_balance()["equity"]
        estimated_price = self._estimate_price(symbol, limit_price)

        decision = self.risk_manager.validate_order(
            symbol=symbol,
            qty=qty,
            side=side_l,
            equity=equity,
            estimated_price=estimated_price,
            entry_reason=entry_reason,
        )
        if not decision.allowed:
            self.journal.log_order(
                order_id=None,
                symbol=symbol,
                side=side_l,
                qty=qty,
                order_type=order_type_l,
                price=estimated_price,
                status="rejected",
                entry_reason=entry_reason,
            )
            raise RiskLimitExceededError(decision.reason)

        if self.dry_run:
            self.journal.log_order(
                order_id="DRY-RUN",
                symbol=symbol,
                side=side_l,
                qty=qty,
                order_type=order_type_l,
                price=estimated_price,
                status="simulated",
                entry_reason=entry_reason,
            )
            return {
                "dry_run": True,
                "symbol": symbol,
                "qty": qty,
                "side": side_l,
                "order_type": order_type_l,
                "estimated_price": estimated_price,
            }

        alpaca_side = AlpacaOrderSide.BUY if side_l == "buy" else AlpacaOrderSide.SELL
        if order_type_l == "market":
            request = MarketOrderRequest(
                symbol=symbol, qty=qty, side=alpaca_side, time_in_force=TimeInForce.DAY
            )
        else:
            request = LimitOrderRequest(
                symbol=symbol,
                qty=qty,
                side=alpaca_side,
                time_in_force=TimeInForce.DAY,
                limit_price=limit_price,
            )

        order = self._client.submit_order(order_data=request)
        self.journal.log_order(
            order_id=str(order.id),
            symbol=symbol,
            side=side_l,
            qty=qty,
            order_type=order_type_l,
            price=estimated_price,
            status=str(getattr(order.status, "value", order.status)),
            entry_reason=entry_reason,
        )
        return order

    def cancel_order(self, order_id: str) -> None:
        self._client.cancel_order_by_id(order_id)

    def get_order_status(self, order_id: str) -> str:
        order = self._client.get_order_by_id(order_id)
        return str(getattr(order.status, "value", order.status))
