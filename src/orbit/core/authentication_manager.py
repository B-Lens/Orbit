"""
authentication_manager
======================

Provides :class:`AuthenticationManager`, the single place where Binance
Spot and Futures API clients are created and shared with the rest of the
core module.

Dependencies (Binance clients, application config) can be **injected**
through the constructor for easier testing and looser coupling.
"""

import os
import locale
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from binance.spot import Spot
from binance.um_futures import UMFutures

from config.config import load_config
from orbit.core.exception_manager import ExceptionManager
from orbit.core.execution import ExecutionSettings

logger = logging.getLogger("Orbit")


def _build_spot_client(api_key: Optional[str], secret_key: Optional[str]) -> Spot:
    """Create a :class:`Spot` client, choosing the US endpoint when appropriate."""
    lang, _ = locale.getdefaultlocale()
    if lang == "en_US":
        client = Spot(api_key, secret_key, base_url="https://api.binance.us")
        logger.info("https://api.binance.us :Authenticated")
    else:
        client = Spot(api_key, secret_key)
    return client


def _build_futures_client(
    api_key: Optional[str], secret_key: Optional[str], base_url: Optional[str] = None
) -> UMFutures:
    """Create a :class:`UMFutures` client."""
    kwargs: Dict[str, Any] = {"key": api_key, "secret": secret_key}
    if base_url:
        kwargs["base_url"] = base_url
    return UMFutures(**kwargs)


@dataclass
class AuthenticationManager(ExceptionManager):
    """Binance API authentication and client management.

    By default the class reads API keys from the environment and builds
    Spot / Futures clients automatically.  For **testing** or when you want
    to share a single client across components, pass pre-built instances via
    the constructor:

    .. code-block:: python

        auth = AuthenticationManager(
            spot_client=my_spot,
            futures_client=my_futures,
            config=my_config,
        )

    Args:
        spot_client: Pre-built :class:`Spot` client (optional).
        futures_client: Pre-built :class:`UMFutures` client (optional).
        config: Application configuration dict (optional; loaded from
            ``config.json`` when not supplied).
    """

    def __init__(
        self,
        spot_client: Optional[Spot] = None,
        futures_client: Optional[UMFutures] = None,
        config: Optional[Dict[str, Any]] = None,
        execution_settings: Optional[ExecutionSettings] = None,
    ) -> None:
        super().__init__()

        self.config: Dict[str, Any] = config if config is not None else load_config()

        self.execution_settings = execution_settings or ExecutionSettings.from_env()
        api_key = self.execution_settings.api_key
        secret_key = self.execution_settings.secret_key

        self.client: Spot = spot_client or _build_spot_client(api_key, secret_key)
        self.future_client: UMFutures = futures_client or _build_futures_client(
            api_key, secret_key, self.execution_settings.futures_base_url
        )

        self.trading_pairs: List[str] = self.config["trading_pairs"]
        self.trade_checker_pair: List[str] = self.config["trade_checker_pair"]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def message_handler(self, _: Any, message: Any) -> None:
        """Generic websocket message handler (logs the raw message)."""
        logger.info(message)

    def get_spot_symbol_price(self, symbol: str) -> float:
        """Return the current Spot price for *symbol*."""
        ticker = self.client.ticker_price(symbol=symbol)
        return float(ticker["price"])

    def get_future_symbol_price(self, symbol: str) -> float:
        """Return the current Futures mark price for *symbol*."""
        ticker = self.future_client.ticker_price(symbol=symbol)
        return float(ticker["price"])
