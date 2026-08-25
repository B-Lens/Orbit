"""
order_manager
=============

Provides :class:`OrderManager`, the single gateway for all Binance Futures
order operations: market / limit orders, stop-loss / take-profit placement,
bridge orders, and order modification / cancellation.

All order placements write Redis mappings:
    ``order:{order_id}`` → ``trade_id``

so that :class:`TradeChecker` can resolve any order back to its parent trade.

Dependencies (:class:`MongoHandler`, Binance clients) can be **injected**
through the constructor for easier testing and looser coupling.
"""

import time
import logging
import threading
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, List, Optional, Tuple

from binance.error import ClientError

from orbit.core.authentication_manager import AuthenticationManager
from orbit.core.mongo_handler import MongoHandler
from orbit.core.redis_manager import RedisManager
from orbit.utils.utils import get_indian_time
from orbit.core.plugins import get_swing_sl
from orbit.core.risk_manager import PreTradeRiskGuard
from orbit.core.performance import PerformanceTracker
from orbit.core.execution import ExecutionMode

logger = logging.getLogger("Orbit")


class OrderManager(AuthenticationManager, RedisManager):
    """Binance Futures order lifecycle management.

    Responsibilities:
        - LIMIT and MARKET order placement
        - Algo-conditional SL / TP order placement and cancellation
        - Exchange-filter validation (tick size, step size, notional)
        - Bridge-order workflow (swing-SL based)
        - Order modification and cancellation
        - Writing ``order:{order_id}`` → ``trade_id`` Redis mappings on every
          successful order placement

    Args:
        mongo_handler: Pre-built :class:`MongoHandler` instance.  When
            ``None`` (the default) a new handler is created internally.
        redis_client: Pre-built ``redis.StrictRedis`` connection.  A default
            ``localhost:6379/0`` connection is created when ``None``.
        **auth_kwargs: Forwarded to :class:`AuthenticationManager` (e.g.
            ``spot_client``, ``futures_client``, ``config``).
    """

    FIXED_SPEND_USDT: float = 30.0
    """Default USDT amount allocated per trade."""

    MAX_LOSS_PER_BRIDGE: float = 0.3
    """Maximum acceptable USDT loss for a single bridge order."""

    POSITION_SIZE_BUFFER: float = 0.98
    """Reserve 2% for price movement, fees, and exchange rounding."""

    _exchange_filters_cache: Dict[str, Dict[str, Any]] = {}
    _exchange_filters_lock = threading.RLock()

    def __init__(
        self,
        mongo_handler: Optional[MongoHandler] = None,
        redis_client=None,
        **auth_kwargs: Any,
    ) -> None:
        AuthenticationManager.__init__(self, **auth_kwargs)
        RedisManager.__init__(self, redis_client=redis_client)
        self.risk_guard = PreTradeRiskGuard(self.config.get("risk_policy"))

        if mongo_handler is not None:
            self.mongo_handler: Optional[MongoHandler] = mongo_handler
        else:
            try:
                self.mongo_handler = MongoHandler()
            except Exception as e:
                self.handle_exception(e, "Exception while Creating MongoHandler")
                self.mongo_handler = None

    # -------------------------------------------------------------------------
    # Exchange filters helpers (tick, step, notional)
    # -------------------------------------------------------------------------

    def get_symbol_filters(self, symbol: str) -> Dict[str, Any]:
        """Fetch and cache Binance exchange filters for *symbol*.

        Returns:
            A dict with keys ``PRICE_FILTER``, ``LOT_SIZE``, and
            ``MIN_NOTIONAL`` (each may be ``None`` if absent).

        Raises:
            Exception: If *symbol* is not found in exchange info.
        """
        with self._exchange_filters_lock:
            cached = self._exchange_filters_cache.get(symbol)
        if cached is not None:
            return cached

        fetched = self._fetch_symbol_filters(symbol)
        with self._exchange_filters_lock:
            # Prefer a value another thread may have published while this
            # request was in flight.
            return self._exchange_filters_cache.setdefault(symbol, fetched)

    def _fetch_symbol_filters(self, symbol: str) -> Dict[str, Any]:
        """Retrieve current symbol rules without holding the shared cache lock."""
        info = self.future_client_for(symbol).exchange_info()
        for item in info["symbols"]:
            if item["symbol"] != symbol:
                continue
            filters = item["filters"]

            def _get_filter(ftype: str) -> Optional[Dict[str, Any]]:
                return next(
                    (entry for entry in filters if entry.get("filterType") == ftype),
                    None,
                )

            return {
                "PRICE_FILTER": _get_filter("PRICE_FILTER"),
                "LOT_SIZE": _get_filter("LOT_SIZE"),
                "MIN_NOTIONAL": _get_filter("MIN_NOTIONAL"),
            }
        raise Exception(f"Symbol {symbol} not found in exchange info")

    def refresh_symbol_filters(self, symbol: str) -> Dict[str, Any]:
        """Fetch current rules and replace cached filters only after success."""
        fetched = self._fetch_symbol_filters(symbol)
        with self._exchange_filters_lock:
            self._exchange_filters_cache[symbol] = fetched
        return fetched

    def submit_test_order(self, symbol: str, **params: Any) -> Dict[str, Any]:
        """Validate an order on Futures Testnet without entering the order book."""
        if self.execution_settings.mode_for(symbol) is not ExecutionMode.TESTNET:
            raise ValueError(f"Refusing test-order submission for live asset {symbol}")
        return self._order_client_for(symbol).new_order_test(symbol=symbol, **params)

    def adjust_price_tick(self, symbol: str, price: float) -> float:
        """Round *price* down to the nearest valid tick for *symbol*."""
        filters = self.get_symbol_filters(symbol)
        price_filter = filters.get("PRICE_FILTER")
        if not price_filter:
            return price

        tick = float(price_filter["tickSize"])
        min_price = float(price_filter["minPrice"])

        price = max(price, min_price)
        if tick <= 0:
            return price

        corrected = price - (price % tick)
        return round(corrected, 8)

    def adjust_quantity_step(self, symbol: str, qty: float) -> float:
        """Round *qty* down to the nearest valid step size for *symbol*."""
        filters = self.get_symbol_filters(symbol)
        lot_size = filters.get("LOT_SIZE")
        if not lot_size:
            return qty

        step = float(lot_size["stepSize"])
        min_qty = float(lot_size["minQty"])

        qty = max(qty, min_qty)
        if step <= 0:
            return qty

        quantity_decimal = Decimal(str(qty))
        step_decimal = Decimal(str(step))
        corrected = (quantity_decimal / step_decimal).to_integral_value(
            rounding=ROUND_DOWN
        ) * step_decimal
        return round(float(corrected), 8)

    def validate_notional(self, symbol: str, price: float, qty: float) -> bool:
        """Return ``True`` if ``price * qty`` meets the MIN_NOTIONAL filter."""
        filters = self.get_symbol_filters(symbol)
        min_notional_filter = filters.get("MIN_NOTIONAL")
        if not min_notional_filter:
            return True

        min_notional = float(min_notional_filter["notional"])
        return (price * qty) >= min_notional

    # -------------------------------------------------------------------------
    # General helpers
    # -------------------------------------------------------------------------

    def get_symbol_price(self, symbol: str) -> float:
        """Return the current Futures ticker price for *symbol*."""
        ticker = self.future_client_for(symbol).ticker_price(symbol=symbol)
        return float(ticker["price"])

    def get_future_symbol_price(self, symbol: str) -> float:
        """Backwards-compatible alias for :meth:`get_symbol_price`."""
        return self.get_symbol_price(symbol)

    def get_usdt_balance(self, symbol: Optional[str] = None) -> float:
        """Return the total USDT wallet balance on the Futures account."""
        client = self.future_client_for(symbol) if symbol else self.future_client
        account_info = client.account()
        return float(account_info["totalWalletBalance"])

    def get_available_usdt_balance(self, symbol: Optional[str] = None) -> float:
        """Return unreserved USDT margin available on the Futures account."""
        client = self.future_client_for(symbol) if symbol else self.future_client
        account_info = client.account()
        return float(account_info["availableBalance"])

    def get_daily_net_pnl(self, symbol: Optional[str] = None) -> float:
        """Synchronize today's exchange income before applying the loss halt."""
        start_ms = int(
            datetime.now(timezone.utc)
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .timestamp()
            * 1000
        )
        client = self.future_client_for(symbol) if symbol else self.future_client
        tracker = PerformanceTracker(
            client,
            self.mongo_handler,
            self.execution_settings.mode_for(symbol or "").value,
        )
        return tracker.sync(start_ms).net_pnl

    def _record_order_rejection(
        self,
        trade_id: Optional[str],
        reason: str,
        **details: Any,
    ) -> None:
        """Attach an exact execution rejection to its immutable decision row."""
        if trade_id and self.mongo_handler is not None:
            self.mongo_handler.append_decision_event(
                trade_id,
                {"status": "order_rejected", "reason": reason, **details},
            )

    def fixed_asset_allocated(self, symbol: str, price: float) -> float:
        """Compute the coin quantity affordable from the fixed USDT allocation.

        Args:
            symbol: Trading pair.
            price: Current coin price in USDT.

        Returns:
            The computed coin quantity.  Returns ``0.0`` when the wallet
            balance is insufficient.
        """
        usdt_balance = self.get_usdt_balance(symbol)
        amount_to_spend = self.config["FIXED_TRADE_AMOUNT"].get(symbol, self.FIXED_SPEND_USDT)

        if usdt_balance <= 0 or usdt_balance < amount_to_spend:
            msg = (
                f"Insufficient USDT balance for fixed allocation. "
                f"Balance: {usdt_balance}, required: {amount_to_spend}"
            )
            logger.warning(msg)
            self.send_alerts(
                data=None,
                description="Insufficient Wallet balance",
                fields={"balance": usdt_balance, "required": amount_to_spend},
            )
            return 0.0

        return amount_to_spend / price

    @staticmethod
    def _get_opposite_side(side: str) -> str:
        """Return the opposite order side (``'BUY'`` ↔ ``'SELL'``)."""
        return "SELL" if side == "BUY" else "BUY"

    # -------------------------------------------------------------------------
    # SL / TP orders
    # -------------------------------------------------------------------------

    def place_algo_conditional_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        stop_price: float,
        quantity: float,
        position_side: Optional[str] = None,
        close_position: bool = False,
        trade_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Place an algo-conditional order (SL or TP) via ``POST /fapi/v1/algoOrder``.

        On success the ``order:{algo_id}`` → ``trade_id`` mapping is written
        to Redis when *trade_id* is provided.

        Args:
            symbol: Trading pair.
            side: ``"BUY"`` or ``"SELL"``.
            order_type: E.g. ``"STOP_MARKET"`` or ``"TAKE_PROFIT_MARKET"``.
            stop_price: Trigger price.
            quantity: Order quantity (ignored when *close_position* is ``True``).
            position_side: Hedge-mode position side (optional).
            close_position: When ``True`` the order closes the full position.
            trade_id: Parent trade identifier used to write the Redis mapping.

        Returns:
            The raw API response dict.
        """
        params = {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "triggerPrice": str(stop_price),
            "workingType": "MARK_PRICE",
            "recvWindow": 60000,
        }

        if close_position:
            params["closePosition"] = "true"
        else:
            params["quantity"] = str(quantity)

        if position_side:
            params["positionSide"] = position_side

        logger.info(f"[ALGO ORDER REQUEST] {params}")
        resp = self._order_client_for(symbol).sign_request(
            "POST", "/fapi/v1/algoOrder", params
        )
        logger.info(f"[ALGO ORDER RESPONSE] {resp}")

        # Register order → trade mapping via RedisManager
        if resp and trade_id:
            algo_id = str(resp.get("algoId", ""))
            if algo_id:
                self.register_order(algo_id, trade_id)

        return resp

    def cancel_algo_conditional_order(
        self,
        symbol: str,
        algo_id: str,
    ) -> Dict[str, Any]:
        """Cancel a conditional algo order via ``DELETE /fapi/v1/algoOrder``.

        Also removes the ``order:{algo_id}`` Redis mapping.

        Args:
            symbol: Trading pair.
            algo_id: The ``algoId`` returned when the order was placed.

        Returns:
            The raw API response dict.
        """
        params = {"symbol": symbol, "algoId": algo_id, "recvWindow": 60000}
        logger.info(f"[ALGO CANCEL REQUEST] {params}")
        resp = self.future_client_for(symbol).sign_request(
            "DELETE", "/fapi/v1/algoOrder", params
        )
        logger.info(f"[ALGO CANCEL RESPONSE] {resp}")

        self.deregister_order(str(algo_id))

        return resp

    def place_sl_order(
        self,
        symbol: str,
        side: str,
        stoploss_price: float,
        quantity: float,
        trade_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Place a ``STOP_MARKET`` stop-loss order via the Algo Order API.

        Args:
            symbol: Trading pair.
            side: ``"BUY"`` or ``"SELL"`` (the exit side).
            stoploss_price: Trigger price for the stop.
            quantity: Position quantity to close.
            trade_id: Parent trade identifier for Redis mapping.

        Returns:
            The API response dict, or ``None`` on failure.
        """
        return self._place_exit_order(
            symbol=symbol, side=side, price=stoploss_price, quantity=quantity,
            trade_id=trade_id, order_type="STOP_MARKET",
            price_field="stopLossPrice", label="SL",
            notify=self.send_sl_update_notifier,
        )

    def place_target_order(
        self,
        symbol: str,
        side: str,
        target_price: float,
        quantity: float,
        trade_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Place a ``TAKE_PROFIT_MARKET`` order via the Algo Order API.

        Args:
            symbol: Trading pair.
            side: ``"BUY"`` or ``"SELL"`` (the exit side).
            target_price: Trigger price for the take-profit.
            quantity: Position quantity to close.
            trade_id: Parent trade identifier for Redis mapping.

        Returns:
            The API response dict, or ``None`` on failure.
        """
        return self._place_exit_order(
            symbol=symbol, side=side, price=target_price, quantity=quantity,
            trade_id=trade_id, order_type="TAKE_PROFIT_MARKET",
            price_field="targetPrice", label="Target",
            notify=self.send_signal_updates,
        )

    def _place_exit_order(
        self, *, symbol: str, side: str, price: float, quantity: float,
        trade_id: Optional[str], order_type: str, price_field: str,
        label: str, notify: Any,
    ) -> Optional[Dict[str, Any]]:
        """Place a normalized SL/TP order and emit its request and response."""
        try:
            precision = self.config["trading_pairs_precision"][symbol]
            quantity = abs(round(float(quantity), precision))
            quantity = self.adjust_quantity_step(symbol, quantity)
            request = {"symbol": symbol, "side": side, price_field: price, "quantity": quantity}
            notify(data=None, description=f"{label} Order Request for {symbol}", fields=request)
            response = self.place_algo_conditional_order(
                symbol=symbol, side=side, order_type=order_type,
                stop_price=round(price, 1), quantity=quantity,
                trade_id=trade_id or symbol,
            )
            if trade_id and self.mongo_handler is not None:
                self.mongo_handler.append_decision_event(
                    trade_id,
                    {
                        "status": (
                            "protective_order_submitted"
                            if response
                            else "protective_order_failed"
                        ),
                        "protective_order_type": order_type,
                        "order_id": response.get("algoId") if response else None,
                    },
                )
            notify(
                data=None,
                description=f"{label} Order Response for {symbol}",
                fields=response,
            )
            return response
        except ClientError as error:
            if trade_id and self.mongo_handler is not None:
                self.mongo_handler.append_decision_event(
                    trade_id,
                    {
                        "status": "protective_order_failed",
                        "protective_order_type": order_type,
                        "error_code": getattr(error, "error_code", None),
                    },
                )
            self.clientExceptionHandler(
                symbol=symbol, error=error,
                Location=f"OrderManager -> place_{label.lower()}_order",
            )
        except Exception as error:
            if trade_id and self.mongo_handler is not None:
                self.mongo_handler.append_decision_event(
                    trade_id,
                    {
                        "status": "protective_order_failed",
                        "protective_order_type": order_type,
                    },
                )
            self.handle_exception(
                error, f"Unexpected exception in place_{label.lower()}_order"
            )
        return None

    # -------------------------------------------------------------------------
    # Limit order with optional auto SL/TP
    # -------------------------------------------------------------------------

    def calculate_risk_position_size(
        self,
        symbol: str,
        entry_price: float,
        stop_price: float,
        risk_perc: float,
        leverage: int = 1,
    ) -> Tuple[float, float]:
        """Calculate a risk-based position size with Binance filters applied.

        Args:
            symbol: Trading pair (e.g. ``"BTCUSDT"``).
            entry_price: Planned entry price.
            stop_price: Planned stop-loss price.
            risk_perc: Risk as a fraction of equity (``0.01`` = 1 %).
            leverage: Leverage multiplier.

        Returns:
            A ``(quantity, required_margin)`` tuple.
        """
        if (
            entry_price <= 0
            or stop_price is None
            or stop_price <= 0
            or leverage <= 0
            or leverage > self.risk_guard.max_leverage
        ):
            return 0.0, 0.0
        equity = self.get_usdt_balance(symbol)
        available_margin = self.get_available_usdt_balance(symbol)
        if equity <= 0 or available_margin <= 0:
            return 0.0, 0.0
        effective_risk = min(float(risk_perc), self.risk_guard.max_risk_per_trade_pct)
        risk_value = equity * effective_risk
        stop_distance = abs(entry_price - stop_price)
        if stop_distance <= 0:
            return 0.0, 0.0
        qty_risk = risk_value / stop_distance
        qty_notional = (
            equity * self.risk_guard.max_position_notional_pct / entry_price
        )
        qty_margin = available_margin * leverage / entry_price

        filters = self.get_symbol_filters(symbol)
        min_notional_filter = filters.get("MIN_NOTIONAL")
        min_notional = float(min_notional_filter["notional"]) if min_notional_filter else 5.0
        lot_size_filter = filters.get("LOT_SIZE")
        lot_size_min_qty = (
            float(lot_size_filter["minQty"]) if lot_size_filter else 0.0
        )
        min_qty = max(min_notional / entry_price, lot_size_min_qty)
        qty = min(qty_risk, qty_notional, qty_margin) * self.POSITION_SIZE_BUFFER
        qty = self.adjust_quantity_step(symbol, qty)
        # An exchange minimum must never raise quantity above a safety cap.
        if qty < min_qty:
            return 0.0, 0.0
        required_margin = (entry_price * qty) / leverage
        logger.info(f"Calculated position size for {symbol}: Qty={qty}, Required Margin={required_margin}")
        return qty, required_margin

    def place_order(
        self,
        risk_management: Dict[str, Any],
        symbol: str,
        side: str,
        price: Optional[float] = None,
        sl: Optional[float] = None,
        target: Optional[float] = None,
        leverage: int = 2,
        quantity: Optional[float] = None,
        ros: bool = False,
        trade_id: Optional[str] = None,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[float], Optional[Dict[str, Any]]]:
        """Place a ``LIMIT`` order on Binance Futures with optional SL/TP.

        Args:
            risk_management: Config dict (must contain ``"stop_loss_percent"``).
            symbol: Trading pair (e.g. ``"BTCUSDT"``).
            side: ``"BUY"`` or ``"SELL"``.
            price: Limit price (**required**).
            sl: Explicit stop-loss price; computed from *risk_management* when ``None``.
            target: Explicit take-profit price; no TP placed when ``None``.
            leverage: Leverage to apply on Binance before placing the order.
            quantity: Coin quantity; computed from fixed allocation when ``None``.
            ros: *Return On Signal* mode — when ``True`` the method returns
                immediately after the main order without placing SL/TP.
            trade_id: Parent trade identifier for Redis order mappings.  Falls
                back to *symbol* when ``None``.

        Returns:
            A ``(order_response, used_quantity, field_params)`` tuple.
            All three elements are ``None`` on failure.
        """
        try:
            if price is None:
                logger.error("place_order called without price (LIMIT).")
                self.send_alerts(
                    data=None,
                    description="place_order called without price",
                    fields={"symbol": symbol, "side": side},
                )
                self._record_order_rejection(trade_id, "missing_limit_price")
                return None, None, None

            effective_trade_id = trade_id or symbol

            balance_available = self.get_usdt_balance(symbol)
            available_margin = self.get_available_usdt_balance(symbol)
            qty_from_alloc: float = 0.0

            if sl is not None and symbol in risk_management:
                qty_from_alloc, req_margin = self.calculate_risk_position_size(
                    symbol=symbol, entry_price=price, stop_price=sl,
                    risk_perc=risk_management[symbol], leverage=leverage
                )
                self.send_logs(data=None, description=f"Required margin for {symbol} is {req_margin}", fields=None)
            else:
                qty_from_alloc = self.fixed_asset_allocated(symbol=symbol, price=price)

            if quantity is None:
                quantity = qty_from_alloc

            if quantity <= 0:
                self.send_alerts(
                    data=None,
                    description=f"Not Enough funds for {symbol}, quantity: {quantity}, balance_available: {balance_available}",
                    fields=None,
                )
                self._record_order_rejection(
                    trade_id,
                    "insufficient_funds_or_quantity",
                    balance=balance_available,
                    available_margin=available_margin,
                    quantity=quantity,
                )
                return None, None, None

            precision = self.config["trading_pairs_precision"][symbol]
            quantity_decimal = Decimal(str(quantity))
            precision_unit = Decimal(1).scaleb(-precision)
            quantity = float(
                quantity_decimal.quantize(precision_unit, rounding=ROUND_DOWN)
            )

            if quantity <= 0:
                self.send_alerts(
                    data=None,
                    description=f"Computed quantity <= 0 for {symbol}",
                    fields={"symbol": symbol, "raw_quantity": quantity, "leverage": leverage, "balance_available": balance_available},
                )
                self._record_order_rejection(
                    trade_id, "quantity_non_positive", quantity=quantity
                )
                return None, None, None

            adjusted_price = self.adjust_price_tick(symbol, price)
            if adjusted_price != price:
                logger.warning(f"[{symbol}] Price adjusted for tickSize: {price} -> {adjusted_price}")
            price = adjusted_price

            qty_valid = self.adjust_quantity_step(symbol, quantity)
            if qty_valid != quantity:
                logger.warning(f"[{symbol}] Quantity adjusted for stepSize: {quantity} -> {qty_valid}")
            quantity = qty_valid

            if sl is None:
                logger.error("Order rejected: a stop loss is required by risk policy")
                self._record_order_rejection(trade_id, "stop_loss_required")
                return None, None, None

            risk_decision = self.risk_guard.evaluate(
                equity=balance_available,
                entry_price=price,
                stop_loss=float(sl),
                take_profit=float(target) if target is not None else None,
                quantity=quantity,
                leverage=leverage,
                side=side,
                daily_net_pnl=self.get_daily_net_pnl(symbol),
                available_margin=available_margin,
            )
            if not risk_decision.allowed:
                logger.warning("Order rejected by risk guard: %s", risk_decision.reason)
                self.send_alerts(
                    data=None,
                    description=f"Order rejected by risk guard: {risk_decision.reason}",
                    fields={"symbol": symbol, **risk_decision.metrics},
                )
                self._record_order_rejection(
                    trade_id, risk_decision.reason, **risk_decision.metrics
                )
                return None, None, None

            if not self.validate_notional(symbol, price, quantity):
                filters = self.get_symbol_filters(symbol)
                minimum_notional = (
                    filters["MIN_NOTIONAL"]["notional"]
                    if filters.get("MIN_NOTIONAL")
                    else "N/A"
                )
                logger.error(
                    f"[NOTIONAL ERROR] {symbol} LIMIT order rejected. "
                    f"Required: {minimum_notional}, Got: {price * quantity}"
                )
                self.send_alerts(
                    data=None,
                    description="Order rejected – Notional too small",
                    fields={"symbol": symbol, "price": price, "qty": quantity},
                )
                self._record_order_rejection(
                    trade_id,
                    "minimum_notional",
                    price=price,
                    quantity=quantity,
                    minimum_notional=minimum_notional,
                )
                return None, None, None

            field_params = {
                "symbol": symbol,
                "action": side,
                "quantity": quantity,
                "price": price,
                "WalletBalance": balance_available,
                "orderPlaceTime": get_indian_time(),
                "leverage": leverage,
            }

            self.send_signal_updates(
                data=None,
                description=f"Order request Params for {symbol}",
                fields=field_params,
            )

            futures_client = self._order_client_for(symbol)
            futures_client.change_leverage(symbol=symbol, leverage=leverage, recvWindow=60000)

            params = {
                "symbol": symbol,
                "side": side,
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": str(quantity),
                "price": str(price),
                "recvWindow": 60000,
            }
            if trade_id:
                # Binance client order IDs are capped at 36 characters.
                compact_id = str(trade_id).replace("-", "")[:32]
                params["newClientOrderId"] = f"o{compact_id}"

            order_response = futures_client.new_order(**params)

            if not order_response:
                logger.error(f"Failed to place LIMIT order for {symbol}")
                self._record_order_rejection(trade_id, "empty_exchange_response")
                return None, None, None

            order_id = order_response.get("orderId")
            client_order_id = order_response.get("clientOrderId") or params.get("newClientOrderId")
            if trade_id and self.mongo_handler is not None:
                identity = order_id or client_order_id
                self.mongo_handler.append_decision_event(
                    trade_id,
                    {
                        "event_id": f"order_submitted:{symbol}:{identity}",
                        "status": "order_submitted",
                        "order_id": order_id,
                        "client_order_id": client_order_id,
                    },
                )

            time.sleep(2)

            self.send_signal_updates(
                data=None,
                description=f"{symbol} order placed successfully",
                fields=order_response,
            )

            if ros:
                logger.info(f"ROS mode: returning after main order for {symbol}")
                return order_response, quantity, field_params

            stoploss_price: float
            if sl is not None:
                stoploss_price = sl
            else:
                sl_percent = float(risk_management.get("stop_loss_percent", 0))
                if side == "BUY":
                    stoploss_price = round(price * ((100.0 - sl_percent) / 100.0), 1)
                else:
                    stoploss_price = round(price * ((100.0 + sl_percent) / 100.0), 1)

            sl_target_side = self._get_opposite_side(side)
            self.place_sl_order(symbol, sl_target_side, stoploss_price, quantity, trade_id=effective_trade_id)
            time.sleep(1)

            if target is not None:
                self.place_target_order(symbol, sl_target_side, target, quantity, trade_id=effective_trade_id)

            logger.info(f"Order placed: {order_response}")
            return order_response, quantity, field_params

        except ClientError as error:
            self._record_order_rejection(
                trade_id,
                "exchange_client_error",
                error_code=getattr(error, "error_code", None),
            )
            self.clientExceptionHandler(symbol=symbol, error=error, Location="Order Manager -> place_order")
        except Exception as e:
            self._record_order_rejection(trade_id, "order_exception")
            self.handle_exception(e, context_description="Exception caught while Placing order")

        return None, None, None

    # -------------------------------------------------------------------------
    # Market order
    # -------------------------------------------------------------------------

    def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: Optional[float] = None,
        leverage: int = 1,
    ) -> Optional[Dict[str, Any]]:
        """Place a ``MARKET`` order on Binance Futures.

        Args:
            symbol: Trading pair (e.g. ``"BTCUSDT"``).
            side: ``"BUY"`` or ``"SELL"``.
            quantity: Coin quantity; computed from fixed allocation when ``None``.
            leverage: Multiplier applied to the computed quantity.

        Returns:
            The API response dict, or ``None`` on failure.
        """
        try:
            current_price = self.get_symbol_price(symbol)

            if quantity is None:
                qty_alloc = self.fixed_asset_allocated(symbol=symbol, price=current_price)
                balance = self.get_usdt_balance(symbol)
                if balance < self.FIXED_SPEND_USDT or qty_alloc <= 0:
                    self.send_alerts(
                        data=None,
                        description=f"Not Enough funds for {symbol} market order",
                        fields={"symbol": symbol, "balance": balance, "price": current_price},
                    )
                    return None
                quantity = qty_alloc

            precision = self.config["trading_pairs_precision"][symbol]
            quantity = round(float(quantity) * leverage, precision)

            qty_valid = self.adjust_quantity_step(symbol, quantity)
            if qty_valid != quantity:
                logger.warning(f"[{symbol}] MARKET qty adjusted for stepSize: {quantity} -> {qty_valid}")
            quantity = qty_valid

            if quantity <= 0:
                self.send_alerts(data=None, description=f"Computed MARKET quantity <= 0 for {symbol}", fields={"symbol": symbol})
                return None

            if not self.validate_notional(symbol, current_price, quantity):
                filters = self.get_symbol_filters(symbol)
                min_notional = filters["MIN_NOTIONAL"]["notional"] if filters.get("MIN_NOTIONAL") else "N/A"
                logger.error(f"[NOTIONAL ERROR] {symbol} MARKET order rejected. Required: {min_notional}, Got: {current_price * quantity}")
                self.send_alerts(
                    data=None,
                    description="Market order rejected – Notional too small",
                    fields={"symbol": symbol, "price": current_price, "qty": quantity},
                )
                return None

            market_order_params = {
                "symbol": symbol,
                "side": side,
                "type": "MARKET",
                "quantity": quantity,
                "recvWindow": 60000,
            }

            self.send_signal_updates(data=None, description=f"Market Order request for {symbol}", fields=market_order_params)

            order_response = self._order_client_for(symbol).new_order(**market_order_params)

            if order_response:
                self.send_signal_updates(data=None, description=f"{symbol} market order placed successfully", fields=order_response)

            return order_response

        except ClientError as error:
            self.clientExceptionHandler(symbol=symbol, error=error, Location="Order Manager -> place_market_order")
        except Exception as e:
            self.handle_exception(e, context_description="Exception caught while placing market order")

        return None

    # -------------------------------------------------------------------------
    # Cancel / query orders
    # -------------------------------------------------------------------------

    def cancel_order(self, symbol: str, order_id: int) -> Optional[Dict[str, Any]]:
        """Cancel an open order on Binance Futures.

        Args:
            symbol: Trading pair.
            order_id: The ``orderId`` to cancel.

        Returns:
            The cancellation response dict, or ``None`` on failure.
        """
        try:
            result = self.future_client_for(symbol).cancel_order(
                symbol=symbol, orderId=order_id, recvWindow=60000
            )
            logger.info(f"Order canceled: {result}")
            return result
        except ClientError as error:
            self.clientExceptionHandler(symbol=symbol, error=error, Location="OrderManager -> cancel_order")
        except Exception as e:
            self.handle_exception(e, context_description="Exception caught while Cancelling order")
        return None

    def get_open_orders(self, symbol: str, orderId: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return all orders for *symbol* (optionally filtered by *orderId*).

        Args:
            symbol: Trading pair.
            orderId: When provided, only orders with this ID are returned.

        Returns:
            A list of order dicts (may be empty on error).
        """
        try:
            orders = self.future_client_for(symbol).get_all_orders(
                symbol=symbol, orderId=orderId, recvWindow=60000
            )
            logger.info(f"Open orders: {orders} for symbol {symbol}")
            return orders
        except ClientError as error:
            self.clientExceptionHandler(symbol=symbol, error=error, Location="OrderManager -> get_open_orders")
        except Exception as e:
            self.handle_exception(e, context_description="Exception caught while fetching open order")
        return []

    def get_order(self, symbol: str, order_id: int) -> Dict[str, Any]:
        """Return the current state of one Futures order, including terminal states."""
        try:
            order = self.future_client_for(symbol).query_order(
                symbol=symbol, orderId=order_id, recvWindow=60000
            )
            if isinstance(order, dict):
                return order
            if isinstance(order, list) and order:
                return order[0]
        except ClientError as error:
            self.clientExceptionHandler(
                symbol=symbol, error=error, Location="OrderManager -> get_order"
            )
        except Exception as error:
            self.handle_exception(
                error, context_description="Exception caught while fetching order"
            )
        return {}

    def get_conditional_open_orders(self, symbol: str) -> List[Dict[str, Any]]:
        """Return all **open** conditional (SL/TP) algo orders for *symbol*.

        Only orders with ``algoStatus == "NEW"`` are included.

        Args:
            symbol: Trading pair.

        Returns:
            A list of open algo-order dicts (may be empty).
        """
        max_retries = 3
        retry_delay = 2

        for attempt in range(1, max_retries + 1):
            try:
                orders = self.future_client_for(symbol).sign_request(
                    "GET",
                    "/fapi/v1/allAlgoOrders",
                    {"symbol": symbol, "recvWindow": 60000},
                )
                open_orders = [o for o in orders if o.get("algoStatus") == "NEW"]
                logger.info(f"[COND OPEN ORDERS] {symbol} -> {len(open_orders)} orders")
                return open_orders

            except ClientError as error:
                # -1021 = Timestamp outside recvWindow; retry after a short delay
                if error.error_code == -1021 and attempt < max_retries:
                    logger.warning(
                        f"[COND OPEN ORDERS] Timestamp error for {symbol} "
                        f"(attempt {attempt}/{max_retries}), retrying in {retry_delay}s…"
                    )
                    time.sleep(retry_delay)
                    retry_delay *= 2  # exponential back-off
                else:
                    self.clientExceptionHandler(
                        symbol=symbol,
                        error=error,
                        Location="OrderManager -> get_conditional_open_orders",
                    )
                    return []

            except Exception as e:
                self.handle_exception(e, context_description="Exception caught while fetching conditional open orders")
                return []

        return []

    # -------------------------------------------------------------------------
    # Bridge order (swing SL based)
    # -------------------------------------------------------------------------

    def place_bridge_order(
        self,
        risk_management: Dict[str, Any],
        symbol: str,
        side: str,
        price: Optional[float] = None,
        leverage: int = 2,
        trade_id: Optional[str] = None,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[float]]:
        """Place a *bridge* limit order sized by max-loss and swing stop-loss.

        Args:
            risk_management: Config dict with ``"stop_loss_percent"``.
            symbol: Trading pair.
            side: ``"BUY"`` or ``"SELL"``.
            price: Limit price; fetched from the API when ``None``.
            leverage: Leverage multiplier.
            trade_id: Parent trade identifier for Redis order mappings.

        Returns:
            A ``(order_response, used_quantity)`` tuple, or ``(None, None)`` on failure.
        """
        try:
            if price is None:
                price = self.get_future_symbol_price(symbol=symbol)

            effective_trade_id = trade_id or symbol
            future_leverage = 10 if symbol == "BTCUSDT" else leverage

            if self.mongo_handler is None:
                self.send_alerts(data=None, description=f"MongoHandler not available; cannot place bridge order for {symbol}", fields=None)
                return None, None

            existing_data = self.mongo_handler.get_mongo_historical_data(symbol, interval="15m")

            if side == "BUY":
                sl_price = get_swing_sl(df=existing_data, n=5, buy_price=price)
            else:
                sl_price = get_swing_sl(df=existing_data, n=5, sell_price=price)

            sl_percent = float(risk_management.get("stop_loss_percent", 0))

            if sl_price is None:
                self.send_alerts(data=None, description=f"No swing level found for {symbol}. Falling back to percent SL.", fields={"symbol": symbol, "price": price, "side": side})
                if side == "BUY":
                    sl_price = round(price * (1.0 - sl_percent / 100.0), 1)
                else:
                    sl_price = round(price * (1.0 + sl_percent / 100.0), 1)

            sl_price = round(sl_price, 1)
            price_diff = abs(sl_price - price)
            if price_diff <= 0:
                logger.error(f"Computed price_diff <= 0 for bridge order: price={price}, sl_price={sl_price}")
                return None, None

            quantity = (self.MAX_LOSS_PER_BRIDGE / future_leverage) / price_diff

            order_response, used_qty, order_request = self.place_order(
                risk_management=risk_management,
                symbol=symbol,
                side=side,
                price=price,
                leverage=future_leverage,
                quantity=quantity,
                ros=True,
                trade_id=effective_trade_id,
            )

            if not order_response:
                logger.error(f"Bridge order failed for {symbol}")
                return None, None

            order_id = order_response["orderId"]
            start_time = time.time()
            timeout = 300

            while True:
                order_status = self.future_client_for(symbol).get_orders(
                    symbol=symbol, orderId=order_id, recvWindow=60000
                )
                status: Optional[str] = None
                if isinstance(order_status, dict):
                    status = order_status.get("status")
                elif isinstance(order_status, list) and order_status:
                    status = order_status[0].get("status")

                if status == "FILLED":
                    logger.info(f"Bridge order filled for {symbol}, placing SL order at {sl_price}")
                    break

                if time.time() - start_time > timeout:
                    logger.info(f"Timeout reached for bridge order for {symbol}. OrderId={order_id}")
                    break

                time.sleep(2)

            sl_side = self._get_opposite_side(side)
            if used_qty is None:
                used_qty = quantity

            self.place_sl_order(symbol, sl_side, sl_price, used_qty, trade_id=effective_trade_id)
            return order_response, used_qty

        except ClientError as error:
            self.clientExceptionHandler(symbol=symbol, error=error, Location="Order Manager -> place_bridge_order")
        except Exception as e:
            self.handle_exception(e, context_description="Exception caught at place_bridge_order")

        return None, None

    # -------------------------------------------------------------------------
    # Modify existing order
    # -------------------------------------------------------------------------

    def modify_order(
        self,
        symbol: str,
        side: str,
        orderId: int,
        price: float,
        quantity: float,
        order_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Modify an existing Futures order in-place.

        Args:
            symbol: Trading pair.
            side: ``"BUY"`` or ``"SELL"``.
            orderId: The ``orderId`` of the order to modify.
            price: New limit price.
            quantity: New quantity.
            order_type: Human-readable label (e.g. ``"SL"``) used in Discord notifications.

        Returns:
            A list containing the modification response dict(s), or an empty list on failure.
        """
        try:
            precision = self.config["trading_pairs_precision"][symbol]
            quantity = round(float(quantity), precision)
            price = self.adjust_price_tick(symbol, price)
            quantity = self.adjust_quantity_step(symbol, quantity)

            modified_order = self._order_client_for(symbol).modify_order(
                symbol=symbol, side=side, orderId=orderId, price=price, quantity=quantity, recvWindow=60000,
            )

            self.send_active_trades_info(
                data=None,
                description=f"{symbol} {order_type or ''} Modified",
                fields=modified_order,
            )
            return modified_order if isinstance(modified_order, list) else [modified_order]

        except ClientError as error:
            self.clientExceptionHandler(symbol=symbol, error=error, Location="OrderManager -> modify_order")
        except Exception as e:
            self.handle_exception(e, context_description="Exception caught while modifying order")

        return []
