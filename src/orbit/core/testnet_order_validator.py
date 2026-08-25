"""Daily exchange validation for testnet order parameters.

The Binance test-order endpoint validates an order without submitting it to
the matching engine.  This catches stale filters, precision mistakes, and
minimum-notional failures without opening a position.
"""

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from orbit.core.execution import ExecutionMode
from orbit.core.order_manager import OrderManager

logger = logging.getLogger("Orbit")


class TestnetOrderValidator:
    """Validate one synthetic LIMIT order per testnet asset once per UTC day."""

    def __init__(
        self,
        order_manager: OrderManager,
        hour_utc: int = 2,
        minute_utc: int = 7,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 0 <= hour_utc <= 23 or not 0 <= minute_utc <= 59:
            raise ValueError("Testnet validation time must be a valid UTC time")
        self.order_manager = order_manager
        self.hour_utc = hour_utc
        self.minute_utc = minute_utc
        self._now = now
        self._sleep = sleep

    @classmethod
    def from_env(cls, order_manager: OrderManager) -> Optional["TestnetOrderValidator"]:
        """Build the validator unless explicitly disabled."""
        enabled = os.getenv("ORBIT_TESTNET_VALIDATION_ENABLED", "true").lower()
        if enabled not in {"1", "true", "yes", "on"}:
            return None
        raw_time = os.getenv("ORBIT_TESTNET_VALIDATION_TIME_UTC", "02:07")
        try:
            hour, minute = (int(part) for part in raw_time.split(":"))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "ORBIT_TESTNET_VALIDATION_TIME_UTC must use HH:MM"
            ) from exc
        return cls(order_manager, hour, minute)

    def validate_symbol(self, symbol: str) -> dict[str, Any]:
        """Send a normalized LIMIT order to Binance's non-executing endpoint."""
        if (
            self.order_manager.execution_settings.mode_for(symbol)
            is not ExecutionMode.TESTNET
        ):
            raise ValueError(f"Refusing validation for non-testnet asset {symbol}")

        # The normal cache is useful in the hot path, but a daily validation
        # must deliberately retrieve today's exchange filters.
        self.order_manager._exchange_filters_cache.pop(symbol, None)
        price = self.order_manager.adjust_price_tick(
            symbol, self.order_manager.get_symbol_price(symbol)
        )
        quantity = self.order_manager.fixed_asset_allocated(symbol, price)
        precision = self.order_manager.config["trading_pairs_precision"][symbol]
        quantity = self.order_manager.adjust_quantity_step(
            symbol, round(float(quantity), precision)
        )
        if quantity <= 0:
            raise ValueError(f"Calculated quantity is zero for {symbol}")
        if not self.order_manager.validate_notional(symbol, price, quantity):
            filters = self.order_manager.get_symbol_filters(symbol)
            minimum = (filters.get("MIN_NOTIONAL") or {}).get("notional", "unknown")
            raise ValueError(
                f"Minimum notional failed for {symbol}: {price * quantity} < {minimum}"
            )

        params = {
            "symbol": symbol,
            "side": "BUY",
            "type": "LIMIT",
            "timeInForce": "GTC",
            "quantity": str(quantity),
            "price": str(price),
            "recvWindow": 60000,
        }
        response = self.order_manager.future_client_for(symbol).new_order_test(**params)
        logger.info("Daily testnet order validation passed for %s: %s", symbol, params)
        return response

    def run_once(self) -> dict[str, bool]:
        """Validate all and only assets configured for testnet execution."""
        results: dict[str, bool] = {}
        for symbol, mode in sorted(
            self.order_manager.execution_settings.asset_modes.items()
        ):
            if mode is not ExecutionMode.TESTNET:
                continue
            try:
                self.validate_symbol(symbol)
                results[symbol] = True
            except Exception as exc:  # Keep checking the remaining assets.
                results[symbol] = False
                logger.exception("Daily testnet order validation failed for %s", symbol)
                self.order_manager.send_alerts(
                    data=None,
                    description=f"Daily testnet order validation failed for {symbol}",
                    fields={"symbol": symbol, "error": str(exc)},
                )
        return results

    def _next_run(self, now: datetime) -> datetime:
        scheduled = now.replace(
            hour=self.hour_utc, minute=self.minute_utc, second=0, microsecond=0
        )
        if scheduled <= now:
            scheduled += timedelta(days=1)
        return scheduled

    def run_forever(self) -> None:
        """Wait for the configured off-peak UTC time and validate each day."""
        while True:
            next_run = self._next_run(self._now())
            while True:
                remaining = (next_run - self._now()).total_seconds()
                if remaining <= 0:
                    break
                self._sleep(min(remaining, 60.0))
            self.run_once()
