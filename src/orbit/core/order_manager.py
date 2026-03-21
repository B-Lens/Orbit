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

import json
import time
import logging
from typing import Any, Dict, List, Optional, Tuple

import redis
from binance.error import ClientError

from orbit.core.authentication_manager import AuthenticationManager
from orbit.core.mongo_handler import MongoHandler
from orbit.utils.utils import get_indian_time
from orbit.core.plugins import get_swing_sl

logger = logging.getLogger("Orbit")

ORDER_KEY_PREFIX = "order:"


def _order_key(order_id: str) -> str:
    return f"{ORDER_KEY_PREFIX}{order_id}"


class OrderManager(AuthenticationManager):
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

    _exchange_filters_cache: Dict[str, Dict[str, Any]] = {}

    def __init__(
        self,
        mongo_handler: Optional[MongoHandler] = None,
        redis_client: Optional[redis.StrictRedis] = None,
        **auth_kwargs: Any,
    ) -> None:
        super().__init__(**auth_kwargs)

        if mongo_handler is not None:
            self.mongo_handler: Optional[MongoHandler] = mongo_handler
        else:
            try:
                self.mongo_handler = MongoHandler()
            except Exception as e:
                self.handle_exception(e, "Exception while Creating MongoHandler")
                self.mongo_handler = None

        self.redis_client: redis.StrictRedis = redis_client or redis.StrictRedis(
            host="localhost", port=6379, db=0, decode_responses=True
        )

    # -------------------------------------------------------------------------
    # Redis mapping helpers
    # -------------------------------------------------------------------------

    def _register_order(self, order_id: str, trade_id: str) -> None:
        """Persist ``order:{order_id}`` → *trade_id* in Redis."""
        try:
            self.redis_client.set(_order_key(str(order_id)), trade_id)
        except Exception as e:
            self.handle_exception(e, context_description=f"_register_order({order_id})")

    def _deregister_order(self, order_id: str) -> None:
        """Remove the ``order:{order_id}`` key from Redis."""
        try:
            self.redis_client.delete(_order_key(str(order_id)))
        except Exception as e:
            self.handle_exception(e, context_description=f"_deregister_order({order_id})")

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
        if symbol in self._exchange_filters_cache:
            return self._exchange_filters_cache[symbol]

        info = self.future_client.exchange_info()
        for s in info["symbols"]:
            if s["symbol"] == symbol:
                filters = s["filters"]

                def _get_filter(ftype: str) -> Optional[Dict[str, Any]]:
                    for f in filters:
                        if f.get("filterType") == ftype:
                            return f
                    return None

                symbol_filters = {
                    "PRICE_FILTER": _get_filter("PRICE_FILTER"),
                    "LOT_SIZE": _get_filter("LOT_SIZE"),
                    "MIN_NOTIONAL": _get_filter("MIN_NOTIONAL"),
                }
                self._exchange_filters_cache[symbol] = symbol_filters
                return symbol_filters

        raise Exception(f"Symbol {symbol} not found in exchange info")

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

        corrected = qty - (qty % step)
        return round(corrected, 8)

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
        ticker = self.future_client.ticker_price(symbol=symbol)
        return float(ticker["price"])

    def get_future_symbol_price(self, symbol: str) -> float:
        """Backwards-compatible alias for :meth:`get_symbol_price`."""
        return self.get_symbol_price(symbol)

    def get_usdt_balance(self) -> float:
        """Return the total USDT wallet balance on the Futures account."""
        account_info = self.future_client.account()
        return float(account_info["totalWalletBalance"])

    def fixed_asset_allocated(self, symbol: str, price: float) -> float:
        """Compute the coin quantity affordable from the fixed USDT allocation.

        Args:
            symbol: Trading pair.
            price: Current coin price in USDT.

        Returns:
            The computed coin quantity.  Returns ``0.0`` when the wallet
            balance is insufficient.
        """
        usdt_balance = self.get_usdt_balance()
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
        resp = self.future_client.sign_request("POST", "/fapi/v1/algoOrder", params)
        logger.info(f"[ALGO ORDER RESPONSE] {resp}")

        # Register order → trade mapping
        if resp and trade_id:
            algo_id = str(resp.get("algoId", ""))
            if algo_id:
                self._register_order(algo_id, trade_id)

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
        resp = self.future_client.sign_request("DELETE", "/fapi/v1/algoOrder", params)
        logger.info(f"[ALGO CANCEL RESPONSE] {resp}")

        # Clean up order mapping
        self._deregister_order(str(algo_id))

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
        try:
            precision = self.config["trading_pairs_precision"][symbol]
            quantity = abs(round(float(quantity), precision))
            quantity = self.adjust_quantity_step(symbol, quantity)

            sl_req = {
                "symbol": symbol,
                "side": side,
                "stopLossPrice": stoploss_price,
                "quantity": quantity,
            }

            logger.warning(f"[SL REQUEST] {sl_req}")
            self.send_sl_update_notifier(
                data=None,
                description=f"SL Order Request for {symbol}",
                fields=sl_req,
            )

            stop_loss_order = self.place_algo_conditional_order(
                symbol=symbol,
                side=side,
                order_type="STOP_MARKET",
                stop_price=round(stoploss_price, 1),
                quantity=quantity,
                trade_id=trade_id or symbol,
            )

            self.send_sl_update_notifier(
                data=None,
                description=f"SL Order Response for {symbol}",
                fields=stop_loss_order,
            )
            return stop_loss_order

        except ClientError as error:
            self.clientExceptionHandler(symbol=symbol, error=error, Location="OrderManager -> place_sl_order")
        except Exception as e:
            self.handle_exception(e, "Unexpected exception in place_sl_order")

        return None

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
        try:
            precision = self.config["trading_pairs_precision"][symbol]
            quantity = abs(round(float(quantity), precision))
            quantity = self.adjust_quantity_step(symbol, quantity)

            tp_req = {
                "symbol": symbol,
                "side": side,
                "targetPrice": target_price,
                "quantity": quantity,
            }

            self.send_signal_updates(
                data=None,
                description=f"Target Order Request for {symbol}",
                fields=tp_req,
            )

            take_profit_order = self.place_algo_conditional_order(
                symbol=symbol,
                side=side,
                order_type="TAKE_PROFIT_MARKET",
                stop_price=round(target_price, 1),
                quantity=quantity,
                trade_id=trade_id or symbol,
            )

            self.send_signal_updates(
                data=None,
                description=f"Target Order Response for {symbol}",
                fields=take_profit_order,
            )
            return take_profit_order

        except ClientError as error:
            self.clientExceptionHandler(symbol=symbol, error=error, Location="OrderManager -> place_target_order")
        except Exception as e:
            self.handle_exception(e, "Unexpected exception in place_target_order")

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
        qty = 0.002  # temporary fixed quantity for testing
        qty = self.adjust_quantity_step(symbol, qty)
        required_margin = (entry_price * qty) / leverage
        return qty, required_margin

        # -----------------------------
        # 1. Fetch equity
        # -----------------------------
        equity = self.config['FIXED_TRADE_AMOUNT'].get(symbol, self.FIXED_SPEND_USDT)
        risk_value = equity * risk_perc

        if entry_price <= 0 or stop_price <= 0:
            return 0.0, 0.0

        if entry_price > stop_price:
            stop_distance = entry_price - stop_price
        else:
            stop_distance = stop_price - entry_price

        if stop_distance <= 0:
            return 0.0, 0.0

        qty_risk = risk_value / stop_distance

        filters = self.get_symbol_filters(symbol)
        min_notional_filter = filters.get("MIN_NOTIONAL")
        min_notional = float(min_notional_filter["notional"]) if min_notional_filter else 5.0
        min_qty = min_notional / entry_price
        qty = max(qty_risk, min_qty)
        qty = self.adjust_quantity_step(symbol, qty)
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
                return None, None, None

            effective_trade_id = trade_id or symbol

            balance_available = self.get_usdt_balance()
            qty_from_alloc: float = 0.0

            if symbol == 'BTCUSDT':
                qty_from_alloc, req_margin = self.calculate_risk_position_size(
                    symbol=symbol, entry_price=price, stop_price=sl,
                    risk_perc=risk_management[symbol], leverage=leverage
                )
                self.send_logs(data=None, description=f"Required margin for {symbol} is {req_margin}", fields=None)
            else:
                qty_from_alloc = self.fixed_asset_allocated(symbol=symbol, price=price)

            if quantity is None:
                quantity = qty_from_alloc

            if balance_available < self.FIXED_SPEND_USDT or quantity <= 0:
                self.send_alerts(
                    data=None,
                    description=f"Not Enough funds for {symbol}, quantity: {quantity}, balance_available: {balance_available}",
                    fields=None,
                )
                return None, None, None

            precision = self.config["trading_pairs_precision"][symbol]
            quantity = round(float(quantity), precision)

            if quantity <= 0:
                self.send_alerts(
                    data=None,
                    description=f"Computed quantity <= 0 for {symbol}",
                    fields={"symbol": symbol, "raw_quantity": quantity, "leverage": leverage, "balance_available": balance_available},
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

            if not self.validate_notional(symbol, price, quantity):
                filters = self.get_symbol_filters(symbol)
                min_notional = filters["MIN_NOTIONAL"]["notional"] if filters.get("MIN_NOTIONAL") else "N/A"
                logger.error(f"[NOTIONAL ERROR] {symbol} LIMIT order rejected. Required: {min_notional}, Got: {price * quantity}")
                self.send_alerts(
                    data=None,
                    description="Order rejected – Notional too small",
                    fields={"symbol": symbol, "price": price, "qty": quantity},
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

            self.future_client.change_leverage(symbol=symbol, leverage=leverage, recvWindow=60000)

            params = {
                "symbol": symbol,
                "side": side,
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": str(quantity),
                "price": str(price),
                "recvWindow": 60000,
            }

            order_response = self.future_client.new_order(**params)
            time.sleep(2)

            if not order_response:
                logger.error(f"Failed to place LIMIT order for {symbol}")
                return None, None, None

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
            self.clientExceptionHandler(symbol=symbol, error=error, Location="Order Manager -> place_order")
        except Exception as e:
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
                balance = self.get_usdt_balance()
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

            order_response = self.future_client.new_order(**market_order_params)

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
            result = self.future_client.cancel_order(symbol=symbol, orderId=order_id, recvWindow=60000)
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
            orders = self.future_client.get_all_orders(symbol=symbol, orderId=orderId, recvWindow=60000)
            logger.info(f"Open orders: {orders} for symbol {symbol}")
            return orders
        except ClientError as error:
            self.clientExceptionHandler(symbol=symbol, error=error, Location="OrderManager -> get_open_orders")
        except Exception as e:
            self.handle_exception(e, context_description="Exception caught while fetching open order")
        return []

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
                orders = self.future_client.sign_request(
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
                order_status = self.future_client.get_orders(symbol=symbol, orderId=order_id, recvWindow=60000)
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

            modified_order = self.future_client.modify_order(
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
