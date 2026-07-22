import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from alpaca_client.risk_manager import RiskManager


def test_order_within_risk_limit_is_allowed():
    rm = RiskManager(max_risk_per_trade_pct=0.02, daily_max_drawdown_pct=0.05)
    decision = rm.validate_order(
        symbol="AAPL", qty=1, side="buy", equity=100_000, estimated_price=150,
    )
    assert decision.allowed is True


def test_order_exceeding_risk_limit_is_rejected():
    rm = RiskManager(max_risk_per_trade_pct=0.01, daily_max_drawdown_pct=0.05)
    # notional = 100 * 150 = 15000, which is 15% of 100k equity >> 1% cap
    decision = rm.validate_order(
        symbol="AAPL", qty=100, side="buy", equity=100_000, estimated_price=150,
    )
    assert decision.allowed is False
    assert "exceeds max risk per trade" in decision.reason


def test_order_exactly_at_limit_is_allowed():
    rm = RiskManager(max_risk_per_trade_pct=0.01, daily_max_drawdown_pct=0.05)
    # notional = 10 * 100 = 1000 == 1% of 100k equity
    decision = rm.validate_order(
        symbol="AAPL", qty=10, side="buy", equity=100_000, estimated_price=100,
    )
    assert decision.allowed is True


def test_daily_drawdown_halts_new_orders():
    rm = RiskManager(max_risk_per_trade_pct=0.5, daily_max_drawdown_pct=0.05)
    # Establish the day's starting equity.
    first = rm.validate_order(symbol="AAPL", qty=1, side="buy", equity=100_000, estimated_price=10)
    assert first.allowed is True

    # Equity drops 6%, breaching the 5% daily drawdown limit.
    second = rm.validate_order(symbol="AAPL", qty=1, side="buy", equity=94_000, estimated_price=10)
    assert second.allowed is False
    assert "drawdown" in second.reason.lower()

    # Even a tiny, otherwise-compliant order stays blocked once halted.
    third = rm.validate_order(symbol="AAPL", qty=1, side="buy", equity=94_000, estimated_price=1)
    assert third.allowed is False


def test_manual_halt_and_resume():
    rm = RiskManager(max_risk_per_trade_pct=0.5, daily_max_drawdown_pct=0.5)
    rm.halt_trading()
    decision = rm.validate_order(symbol="AAPL", qty=1, side="buy", equity=100_000, estimated_price=10)
    assert decision.allowed is False
    assert rm.is_halted is True

    rm.resume_trading()
    decision = rm.validate_order(symbol="AAPL", qty=1, side="buy", equity=100_000, estimated_price=10)
    assert decision.allowed is True


def test_invalid_config_rejected():
    import pytest

    with pytest.raises(ValueError):
        RiskManager(max_risk_per_trade_pct=0)
    with pytest.raises(ValueError):
        RiskManager(daily_max_drawdown_pct=1.5)
