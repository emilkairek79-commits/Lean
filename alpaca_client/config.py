"""Environment-based configuration for the Alpaca paper trading client.

API credentials are read exclusively from environment variables
(APCA_API_KEY_ID, APCA_API_SECRET_KEY). They are never hardcoded and
never written to disk by this module.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv is optional; env vars can be set directly
    load_dotenv = None

PAPER_BASE_URL = "https://paper-api.alpaca.markets"


class ConfigError(Exception):
    """Raised when required Alpaca configuration is missing or invalid."""


@dataclass(frozen=True)
class AlpacaConfig:
    api_key: str
    api_secret: str
    base_url: str = PAPER_BASE_URL


def load_config(env_path: str | Path | None = None) -> AlpacaConfig:
    """Load Alpaca credentials from environment variables (optionally via a .env file).

    Raises ConfigError if APCA_API_KEY_ID / APCA_API_SECRET_KEY are not set.
    """
    if load_dotenv is not None:
        load_dotenv(dotenv_path=env_path)

    api_key = os.environ.get("APCA_API_KEY_ID")
    api_secret = os.environ.get("APCA_API_SECRET_KEY")

    if not api_key or not api_secret:
        raise ConfigError(
            "Missing Alpaca API credentials. Set APCA_API_KEY_ID and "
            "APCA_API_SECRET_KEY as environment variables (see .env.example)."
        )

    return AlpacaConfig(api_key=api_key, api_secret=api_secret, base_url=PAPER_BASE_URL)
