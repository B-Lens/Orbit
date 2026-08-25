"""Daily end-to-end order validation on Binance Futures Testnet.

The validator places a deliberately off-market LIMIT order and immediately
cancels it.  It never operates on a live-configured asset.
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
        price_discount_pct: float = 2.0,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 0 <= hour_utc <= 23 or not 0 <= minute_utc <= 59:
            raise ValueError("Testnet validation time must be a valid UTC time")
        if not 1.0 <= price_discount_pct <= 10.0:
            raise ValueError("Testnet validation price discount must be 1-10 percent")
        self.order_manager = order_manager
        self.hour_utc = hour_utc
        self.minute_utc = minute_utc
        self.price_discount_pct = price_discount_pct
        self._now = now
        self._sleep = sleep

    @classmethod
    def from_env(cls, order_manager: OrderManager) -> Optional["TestnetOrderValidator"]:
        """Build the validator unless explicitly disabled."""
        enabled = os.getenv("ORBIT_TESTNET_VALIDATION_ENABLED", "true").lower()
        if enabled in {"0", "false", "no", "off"}:
            return None
        if enabled not in {"1", "true", "yes", "on"}:
            raise ValueError(
                "ORBIT_TESTNET_VALIDATION_ENABLED must be true or false"
            )
        raw_time = os.getenv("ORBIT_TESTNET_VALIDATION_TIME_UTC", "02:07")
        raw_discount = os.getenv(
            "ORBIT_TESTNET_VALIDATION_PRICE_DISCOUNT_PCT", "2"
        )
        try:
            hour, minute = (int(part) for part in raw_time.split(":"))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "ORBIT_TESTNET_VALIDATION_TIME_UTC must use HH:MM"
            ) from exc
        try:
            discount = float(raw_discount)
        except ValueError as exc:
            raise ValueError(
                "ORBIT_TESTNET_VALIDATION_PRICE_DISCOUNT_PCT must be numeric"
            ) from exc
        return cls(order_manager, hour, minute, discount)

    def validate_symbol(self, symbol: str) -> dict[str, Any]:
        """Place and cancel one safely off-market testnet LIMIT order."""
        if (
            self.order_manager.execution_settings.mode_for(symbol)
            is not ExecutionMode.TESTNET
        ):
            raise ValueError(f"Refusing validation for non-testnet asset {symbol}")

        open_orders = self.order_manager.get_current_open_orders(symbol)
        if open_orders:
            logger.info(
                "Skipping daily validation for %s because %s order(s) are open",
                symbol,
                len(open_orders),
            )
            return {"status": "SKIPPED", "reason": "open_order_present"}

        # A daily validation deliberately retrieves today's exchange filters.
        self.order_manager.refresh_symbol_filters(symbol)
        market_price = self.order_manager.get_symbol_price(symbol)
        price = self.order_manager.adjust_price_tick(
            symbol, market_price * (1.0 - self.price_discount_pct / 100.0)
        )
        quantity = self.order_manager.fixed_asset_allocated(symbol, price)
        intended_quantity = float(quantity)
        precision = self.order_manager.config.get("trading_pairs_precision", {}).get(
            symbol
        )
        if precision is not None:
            quantity = round(float(quantity), int(precision))
        quantity = self.order_manager.adjust_quantity_step(symbol, quantity)
        if quantity <= 0:
            raise ValueError(f"Calculated quantity is zero for {symbol}")
        if quantity > intended_quantity:
            raise ValueError(
                f"Exchange normalization enlarged {symbol} quantity beyond allocation: "
                f"{intended_quantity} -> {quantity}"
            )
        if not self.order_manager.validate_notional(symbol, price, quantity):
            filters = self.order_manager.get_symbol_filters(symbol)
            minimum = (filters.get("MIN_NOTIONAL") or {}).get("notional", "unknown")
            raise ValueError(
                f"Minimum notional failed for {symbol}: {price * quantity} < {minimum}"
            )

        params = {
            "side": "BUY",
            "type": "LIMIT",
            # Post-only prevents an order that crossed during request transit
            # from immediately taking liquidity and filling.
            "timeInForce": "GTX",
            "quantity": str(quantity),
            "price": str(price),
            "recvWindow": 60000,
        }
        response = self.order_manager.submit_validation_order(symbol, **params)
        order_id = response.get("orderId")
        if order_id is None:
            raise RuntimeError(f"Validation order for {symbol} returned no orderId")

        cancellation = self._cancel_validation_order(symbol, int(order_id))
        logger.info(
            "Daily testnet order validation passed and order %s was canceled for %s",
            order_id,
            symbol,
        )
        return {"order": response, "cancellation": cancellation}

    def _cancel_validation_order(
        self, symbol: str, order_id: int
    ) -> dict[str, Any]:
        """Retry cancellation and accept a queried CANCELED terminal state."""
        for attempt in range(3):
            cancellation = self.order_manager.cancel_order(symbol, order_id)
            if cancellation and cancellation.get("status") == "CANCELED":
                return cancellation

            state = self.order_manager.get_order(symbol, order_id)
            status = state.get("status") if isinstance(state, dict) else None
            if status == "CANCELED":
                return state
            if status in {"FILLED", "EXPIRED", "REJECTED"}:
                raise RuntimeError(
                    f"Validation order {order_id} for {symbol} reached {status}"
                )
            if attempt < 2:
                self._sleep(1.0)

        raise RuntimeError(
            f"Validation order {order_id} for {symbol} was not canceled after 3 attempts"
        )

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
