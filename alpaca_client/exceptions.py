class AlpacaClientError(Exception):
    """Base exception for the alpaca_client package."""


class OrderValidationError(AlpacaClientError):
    """Raised when an order's parameters are invalid (bad side, qty, missing limit_price, ...)."""


class RiskLimitExceededError(AlpacaClientError):
    """Raised when an order is rejected by the risk manager."""
