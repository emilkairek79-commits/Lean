import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from alpaca_client.exceptions import OrderValidationError, RiskLimitExceededError
from alpaca_client.journal import TradeJournal
from alpaca_client.risk_manager import RiskManager
from alpaca_client.trading_client import TradingClient


class FakeAlpacaTradingClient:
    def __init__(self, equity=100_000, buying_power=50_000, cash=100_000):
        self._equity = equity
        self._buying_power = buying_power
        self._cash = cash
        self.submitted_orders = []

    def get_account(self):
        return SimpleNamespace(equity=self._equity, buying_power=self._buying_power, cash=self._cash)

    def get_all_positions(self):
        return []

    def submit_order(self, order_data):
        self.submitted_orders.append(order_data)
        return SimpleNamespace(id="fake-order-id", status=SimpleNamespace(value="accepted"))

    def cancel_order_by_id(self, order_id):
        self.cancelled = order_id

    def get_order_by_id(self, order_id):
        return SimpleNamespace(status=SimpleNamespace(value="filled"))


class FakeDataClient:
    def get_stock_latest_quote(self, request):
        symbol = request.symbol_or_symbols
        return {symbol: SimpleNamespace(bid_price=99.5, ask_price=100.5)}


@pytest.fixture
def client(tmp_path):
    journal = TradeJournal(backend="csv", path=tmp_path / "trades.csv")
    risk_manager = RiskManager(max_risk_per_trade_pct=0.01, daily_max_drawdown_pct=0.05, journal=journal)
    return TradingClient(
        risk_manager=risk_manager,
        journal=journal,
        dry_run=True,
        trading_client=FakeAlpacaTradingClient(),
        data_client=FakeDataClient(),
    )


def test_dry_run_does_not_submit_real_order(client):
    result = client.place_order("AAPL", qty=1, side="buy")
    assert result["dry_run"] is True
    # The underlying fake alpaca client must never see a submit_order call.
    assert client._client.submitted_orders == []


def test_dry_run_rejects_order_exceeding_risk_limit(client):
    with pytest.raises(RiskLimitExceededError):
        client.place_order("AAPL", qty=1000, side="buy")
    assert client._client.submitted_orders == []


def test_live_mode_submits_order_via_underlying_client(tmp_path):
    journal = TradeJournal(backend="csv", path=tmp_path / "trades.csv")
    risk_manager = RiskManager(max_risk_per_trade_pct=0.5, daily_max_drawdown_pct=0.5, journal=journal)
    fake_alpaca = FakeAlpacaTradingClient()
    live_client = TradingClient(
        risk_manager=risk_manager,
        journal=journal,
        dry_run=False,
        trading_client=fake_alpaca,
        data_client=FakeDataClient(),
    )
    order = live_client.place_order("AAPL", qty=1, side="buy")
    assert order.id == "fake-order-id"
    assert len(fake_alpaca.submitted_orders) == 1


def test_invalid_side_raises_validation_error(client):
    with pytest.raises(OrderValidationError):
        client.place_order("AAPL", qty=1, side="sideways")


def test_invalid_qty_raises_validation_error(client):
    with pytest.raises(OrderValidationError):
        client.place_order("AAPL", qty=0, side="buy")


def test_limit_order_without_price_raises_validation_error(client):
    with pytest.raises(OrderValidationError):
        client.place_order("AAPL", qty=1, side="buy", order_type="limit")


def test_get_account_balance(client):
    balance = client.get_account_balance()
    assert balance == {"equity": 100_000, "buying_power": 50_000, "cash": 100_000}


def test_cancel_and_status(tmp_path):
    journal = TradeJournal(backend="csv", path=tmp_path / "trades.csv")
    fake_alpaca = FakeAlpacaTradingClient()
    live_client = TradingClient(
        risk_manager=RiskManager(journal=journal),
        journal=journal,
        dry_run=False,
        trading_client=fake_alpaca,
        data_client=FakeDataClient(),
    )
    live_client.cancel_order("some-id")
    assert fake_alpaca.cancelled == "some-id"
    assert live_client.get_order_status("some-id") == "filled"
