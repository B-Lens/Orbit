# src/order_manager.py
import os
import sys
import time
import logging
from typing import Optional, Tuple, Dict, Any, List

from binance.error import ClientError

from orbit.core.authentication_manager import AuthenticationManager
from orbit.core.mongo_handler import MongoHandler
from orbit.utils.utils import get_indian_time
from orbit.core.plugins import get_swing_sl


logger = logging.getLogger("Orbit")


class OrderManager(AuthenticationManager):
    """
    Handles all order-related operations against Binance Futures:
    - Market / Limit orders
    - Stop-loss / Take-profit orders
    - Bridge orders based on swing SL
    """

    FIXED_SPEND_USDT: float = 30.0      # Fixed capital per trade
    MAX_LOSS_PER_BRIDGE: float = 0.3    # Max loss in USDT for bridge orders

    # cache for exchange filters per symbol
    _exchange_filters_cache: Dict[str, Dict[str, Any]] = {}

    def __init__(self):
        super().__init__()
        try:
            self.mongo_handler = MongoHandler()
        except Exception as e:
            # Do not crash if Mongo is unavailable; just log & report
            self.handle_exception(
                e, "Exception while Creating MongoHandler"
            )
            self.mongo_handler = None

    # -------------------------------------------------------------------------
    # Exchange filters helpers (tick, step, notional)
    # -------------------------------------------------------------------------

    def get_symbol_filters(self, symbol: str) -> Dict[str, Any]:
        """
        Fetch and cache exchange filters for the symbol.
        Includes: tick size, step size, minQty, minNotional.
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

                price_filter = _get_filter("PRICE_FILTER")
                lot_size_filter = _get_filter("LOT_SIZE")
                min_notional_filter = _get_filter("MIN_NOTIONAL")

                symbol_filters = {
                    "PRICE_FILTER": price_filter,
                    "LOT_SIZE": lot_size_filter,
                    "MIN_NOTIONAL": min_notional_filter,
                }
                self._exchange_filters_cache[symbol] = symbol_filters
                return symbol_filters

        raise Exception(f"Symbol {symbol} not found in exchange info")

    def adjust_price_tick(self, symbol: str, price: float) -> float:
        """Snap price to Binance tick size."""
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
        """Snap quantity to Binance step size and minQty."""
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
        """Validate if price * qty meets Binance MIN_NOTIONAL (if present)."""
        filters = self.get_symbol_filters(symbol)
        min_notional_filter = filters.get("MIN_NOTIONAL")
        if not min_notional_filter:
            # Some products might not use MIN_NOTIONAL; if so, skip validation
            return True

        min_notional = float(min_notional_filter["notional"])
        notional = price * qty
        return notional >= min_notional

    # -------------------------------------------------------------------------
    # General helpers
    # -------------------------------------------------------------------------

    def get_symbol_price(self, symbol: str) -> float:
        """Get current futures price for a symbol."""
        ticker = self.future_client.ticker_price(symbol=symbol)
        current_price = float(ticker["price"])
        return current_price

    def get_future_symbol_price(self, symbol: str) -> float:
        """
        Backwards-compatible helper.
        If other parts of the code call get_future_symbol_price, this keeps them working.
        """
        return self.get_symbol_price(symbol)

    def get_usdt_balance(self) -> float:
        """Get total USDT wallet balance on futures account."""
        account_info = self.future_client.account()
        balance = float(account_info["totalWalletBalance"])
        return balance

    def get_fixed_allocation(self, symbol: str, price: float) -> Tuple[float, float]:
        """
        Decide quantity based on a fixed USDT amount.

        Returns:
            (coin_qty, usdt_balance)
            coin_qty may be 0 if balance is insufficient.
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
            return 0.0, usdt_balance

        coin_qty = amount_to_spend / price
        return coin_qty

    @staticmethod
    def _get_opposite_side(side: str) -> str:
        """Return the opposite order side ('BUY' ↔ 'SELL')."""
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
        position_side: str = None,
        close_position: bool = False,
    ) -> Dict[str, Any]:
        """
        Universal handler for SL/TP conditional orders using
        POST /fapi/v1/algoOrder (algoType=CONDITIONAL)
        """
        params = {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "triggerPrice": str(stop_price),
            "workingType": "MARK_PRICE",
        }

        # Either qty OR closePosition
        if close_position:
            params["closePosition"] = "true"
        else:
            params["quantity"] = str(quantity)

        # Hedge mode support
        if position_side:
            params["positionSide"] = position_side

        # Raw request
        logger.info(f"[ALGO ORDER REQUEST] {params}")

        resp = self.future_client.sign_request(
            "POST",
            "/fapi/v1/algoOrder",
            params,
        )

        logger.info(f"[ALGO ORDER RESPONSE] {resp}")
        return resp
    
    def cancel_algo_conditional_order(
        self,
        symbol: str,
        algo_id: str,
    ) -> Dict[str, Any]:
        """
        Cancel a Binance Futures CONDITIONAL algo order
        DELETE /fapi/v1/algoOrder
        """

        params = {
            "symbol": symbol,
            "algoId": algo_id,
        }

        logger.info(f"[ALGO CANCEL REQUEST] {params}")

        resp = self.future_client.sign_request(
            "DELETE",
            "/fapi/v1/algoOrder",
            params,
        )

        logger.info(f"[ALGO CANCEL RESPONSE] {resp}")
        return resp


    def place_sl_order(
        self,
        symbol: str,
        side: str,
        stoploss_price: float,
        quantity: float,
    ) -> Optional[Dict[str, Any]]:
        """Place STOP_MARKET SL using new Algo Order API"""
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
            )

            self.send_sl_update_notifier(
                data=None,
                description=f"SL Order Response for {symbol}",
                fields=stop_loss_order,
            )
            return stop_loss_order

        except ClientError as error:
            self.clientExceptionHandler(
                symbol=symbol, error=error,
                Location="OrderManager -> place_sl_order",
            )
        except Exception as e:
            self.handle_exception(
                e, "Unexpected exception in place_sl_order"
            )

        return None

    def place_target_order(
        self,
        symbol: str,
        side: str,
        target_price: float,
        quantity: float,
    ) -> Optional[Dict[str, Any]]:
        """Place TAKE_PROFIT_MARKET order using Algo API"""
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
            )

            self.send_signal_updates(
                data=None,
                description=f"Target Order Response for {symbol}",
                fields=take_profit_order,
            )
            return take_profit_order

        except ClientError as error:
            self.clientExceptionHandler(
                symbol=symbol, error=error,
                Location="OrderManager -> place_target_order",
            )
        except Exception as e:
            self.handle_exception(
                e, "Unexpected exception in place_target_order"
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
    ) -> float:
        """
        Calculate risk-based position size with Binance filters applied.

        Args:
            symbol: e.g. "BTCUSDT"
            entry_price: order entry price
            stop_price: stop-loss price
            risk_perc: risk % of equity (0.01 = 1%)
            leverage: leverage multiplier

        Returns:
            final_quantity (float)
        """
        qty = 0.002 # temporary fixed quantity for testing
        qty = self.adjust_quantity_step(symbol, qty)
        required_margin = (entry_price * qty) / leverage
        return qty, required_margin

        # -----------------------------
        # 1. Fetch equity
        # -----------------------------
        equity = self.config['FIXED_TRADE_AMOUNT'].get(symbol, self.FIXED_SPEND_USDT)
        risk_value = equity * risk_perc   # USDT to risk

        # -----------------------------
        # 2. Compute stop distance
        # -----------------------------
        if entry_price <= 0:
            return 0.0

        if stop_price <= 0:
            return 0.0

        if entry_price > stop_price:          # LONG
            stop_distance = entry_price - stop_price
        else:                                  # SHORT
            stop_distance = stop_price - entry_price

        if stop_distance <= 0:
            return 0.0

        # -----------------------------
        # 3. Base quantity from risk
        # -----------------------------
        qty_risk = risk_value / stop_distance

        # -----------------------------
        # 5. Binance MIN_NOTIONAL rule
        # -----------------------------
        filters = self.get_symbol_filters(symbol)
        min_notional_filter = filters.get("MIN_NOTIONAL")

        if min_notional_filter:
            min_notional = float(min_notional_filter["notional"])
        else:
            min_notional = 5.0                   # Default fallback

        min_qty = min_notional / entry_price     # Minimum allowed quantity

        qty = max(qty_risk, min_qty)

        # -----------------------------
        # 6. Adjust to LOT_SIZE (step and minQty)
        # -----------------------------
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
    ) -> Tuple[Optional[Dict[str, Any]], Optional[float], Optional[Dict[str, Any]]]:
        """
        Place a LIMIT order on Binance Futures.

        Args:
            risk_management: dict with at least "stop_loss_percent"
            symbol: e.g. "BTCUSDT"
            side: "BUY" or "SELL"
            price: limit price (required)
            sl: explicit stop-loss price; if None, computed from risk_management
            target: explicit take-profit price; if None, no TP unless user sets
            leverage: leverage to apply
            quantity: if None, computed from fixed allocation
            ros: if True, return immediately after main order without placing SL/TP

        Returns:
            (order_response, used_quantity, field_params)
        """
        try:
            if price is None:
                msg = "place_order called without price (LIMIT). Use place_market_order for market trades."
                logger.error(msg)
                self.send_alerts(
                    data=None,
                    description="place_order called without price",
                    fields={"symbol": symbol, "side": side},
                )
                return None, None, None

            # Decide quantity if not provided
            balance_available = self.get_usdt_balance()
            qty_from_alloc: float = 0.0
            
            if symbol  == 'BTCUSDT':
                qty_from_alloc, req_margin = self.calculate_risk_position_size(symbol=symbol, entry_price=price, stop_price=sl, risk_perc=risk_management[symbol], leverage=leverage)
                self.send_logs(data=None, description=f"Requird margin for {symbol} is {req_margin}", fields=None)
            else:
                qty_from_alloc = self.get_fixed_allocation(symbol=symbol, price=price)
            
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
                    fields={
                        "symbol": symbol,
                        "raw_quantity": quantity,
                        "leverage": leverage,
                        "balance_available": balance_available,
                    },
                )
                return None, None, None

            # ---------------------------
            # VALIDATE FINAL PRICE / QTY
            # ---------------------------

            # Adjust price according to tick-size
            adjusted_price = self.adjust_price_tick(symbol, price)
            if adjusted_price != price:
                logger.warning(f"[{symbol}] Price adjusted for tickSize: {price} -> {adjusted_price}")
            price = adjusted_price

            # Adjust quantity according to step-size
            qty_valid = self.adjust_quantity_step(symbol, quantity)
            if qty_valid != quantity:
                logger.warning(f"[{symbol}] Quantity adjusted for stepSize: {quantity} -> {qty_valid}")
            quantity = qty_valid

            # Validate notional
            if not self.validate_notional(symbol, price, quantity):
                filters = self.get_symbol_filters(symbol)
                min_notional = filters["MIN_NOTIONAL"]["notional"] if filters.get("MIN_NOTIONAL") else "N/A"
                msg = (
                    f"[NOTIONAL ERROR] {symbol} LIMIT order rejected.\n"
                    f"Required Notional: {min_notional}, Got: {price * quantity}\n"
                    f"Price: {price}, Qty: {quantity}"
                )
                logger.error(msg)

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

            # Set leverage on Binance
            self.future_client.change_leverage(
                symbol=symbol, leverage=leverage, recvWindow=60000
            )

            # Prepare and place the LIMIT order
            params = {
                "symbol": symbol,
                "side": side,
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": str(quantity),
                "price": str(price),
            }

            order_response = self.future_client.new_order(**params)
            time.sleep(2)

            if not order_response:
                logger.error(f"Failed to place LIMIT order for {symbol}")
                return None, None, None

            self.send_signal_updates(
                data=None,
                description=f"{symbol} order placed successfully ",
                fields=order_response,
            )

            # In ROS mode, just return the main order (no SL / TP)
            if ros:
                logger.info(f"ROS mode: returning after main order for {symbol}")
                return order_response, quantity, field_params

            # Compute SL if not explicitly given
            stoploss_price: float
            if sl is not None:
                stoploss_price = sl
            else:
                sl_percent = float(risk_management.get("stop_loss_percent", 0))
                if side == "BUY":
                    stoploss_price = round(
                        price * ((100.0 - sl_percent) / 100.0), 1
                    )
                else:
                    stoploss_price = round(
                        price * ((100.0 + sl_percent) / 100.0), 1
                    )

            sl_target_side = self._get_opposite_side(side)

            # Place SL order
            self.place_sl_order(symbol, sl_target_side, stoploss_price, quantity)
            time.sleep(1)

            # Place TP order if requested
            if target is not None:
                self.place_target_order(symbol, sl_target_side, target, quantity)

            logger.info(f"Order placed: {order_response}")
            return order_response, quantity, field_params

        except ClientError as error:
            self.clientExceptionHandler(
                symbol=symbol,
                error=error,
                Location="Order Manager -> place_order",
            )
        except Exception as e:
            self.handle_exception(
                e, context_description="Exception caught while Placing order"
            )

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
        """
        Place a MARKET order on Binance Futures.

        Args:
            symbol: e.g. "BTCUSDT"
            side: "BUY" or "SELL"
            quantity: if None, computed using fixed allocation at current price
            leverage: optional leverage scaling for quantity
        """
        try:
            # Derive quantity from fixed allocation if not provided
            current_price = self.get_symbol_price(symbol)

            if quantity is None:
                qty_alloc, balance = self.fixed_asset_allocated(symbol=symbol, price=current_price)
                if balance < self.FIXED_SPEND_USDT or qty_alloc <= 0:
                    self.send_alerts(
                        data=None,
                        description=f"Not Enough funds for {symbol} market order",
                        fields={
                            "symbol": symbol,
                            "balance": balance,
                            "price": current_price,
                        },
                    )
                    return None
                quantity = qty_alloc

            precision = self.config["trading_pairs_precision"][symbol]
            quantity = round(float(quantity) * leverage, precision)

            # Adjust quantity according to step-size
            qty_valid = self.adjust_quantity_step(symbol, quantity)
            if qty_valid != quantity:
                logger.warning(f"[{symbol}] MARKET qty adjusted for stepSize: {quantity} -> {qty_valid}")
            quantity = qty_valid

            if quantity <= 0:
                self.send_alerts(
                    data=None,
                    description=f"Computed MARKET quantity <= 0 for {symbol}",
                    fields={"symbol": symbol},
                )
                return None

            # Validate notional using current market price
            if not self.validate_notional(symbol, current_price, quantity):
                filters = self.get_symbol_filters(symbol)
                min_notional = filters["MIN_NOTIONAL"]["notional"] if filters.get("MIN_NOTIONAL") else "N/A"
                msg = (
                    f"[NOTIONAL ERROR] {symbol} MARKET order rejected.\n"
                    f"Required Notional: {min_notional}, Got: {current_price * quantity}\n"
                    f"Price: {current_price}, Qty: {quantity}"
                )
                logger.error(msg)

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
            }

            self.send_signal_updates(
                data=None,
                description=f"Market Order request for {symbol}",
                fields=market_order_params,
            )

            order_response = self.future_client.new_order(**market_order_params)

            if order_response:
                self.send_signal_updates(
                    data=None,
                    description=f"{symbol} market order placed successfully",
                    fields=order_response,
                )

            return order_response

        except ClientError as error:
            self.clientExceptionHandler(
                symbol=symbol,
                error=error,
                Location="Order Manager -> place_market_order",
            )
        except Exception as e:
            self.handle_exception(
                e, context_description="Exception caught while placing market order"
            )

        return None

    # -------------------------------------------------------------------------
    # Cancel / query orders
    # -------------------------------------------------------------------------

    def cancel_order(self, symbol: str, order_id: int) -> Optional[Dict[str, Any]]:
        """
        Cancel an order on the Binance exchange.

        Args:
            symbol: e.g. "BTCUSDT"
            order_id: ID of the order to cancel
        """
        try:
            result = self.future_client.cancel_order(
                symbol=symbol, orderId=order_id
            )
            logger.info(f"Order canceled: {result}")
            return result

        except ClientError as error:
            self.clientExceptionHandler(
                symbol=symbol,
                error=error,
                Location="OrderManager -> cancel_order",
            )
        except Exception as e:
            self.handle_exception(
                e,
                context_description="Exception caught while Cancelling order",
            )

        return None

    def get_open_orders(self, symbol: str, orderId:str= None) -> List[Dict[str, Any]]:
        """
        Get all open orders for a given symbol.

        Args:
            symbol: e.g. "BTCUSDT"
        """
        try:
            orders = self.future_client.get_all_orders(symbol=symbol, orderId=orderId)
            logger.info(f"Open orders: {orders} for symbol {symbol}")
            return orders

        except ClientError as error:
            self.clientExceptionHandler(
                symbol=symbol,
                error=error,
                Location="OrderManager -> get_open_orders",
            )
        except Exception as e:
            self.handle_exception(
                e,
                context_description="Exception caught while fetching open order",
            )

        return []
    
    def get_conditional_open_orders(self, symbol: str) -> List[Dict[str, Any]]:
        """
        Get all OPEN conditional (SL/TP) algo orders for a symbol.

        Args:
            symbol: e.g. "BTCUSDT"
        """
        try:
            params = {
                "symbol": symbol,
            }

            orders = self.future_client.sign_request(
                "GET",
                "/fapi/v1/allAlgoOrders",
                params,
            )

            # Binance returns ALL (NEW, CANCELED, TRIGGERED, EXPIRED)
            open_orders = [
                o for o in orders
                if o.get("algoStatus") == "NEW"
            ]

            logger.info(
                f"[COND OPEN ORDERS] {symbol} -> {len(open_orders)} orders"
            )

            return open_orders

        except ClientError as error:
            self.clientExceptionHandler(
                symbol=symbol,
                error=error,
                Location="OrderManager -> get_conditional_open_orders",
            )

        except Exception as e:
            self.handle_exception(
                e,
                context_description="Exception caught while fetching conditional open orders",
            )

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
    ) -> Tuple[Optional[Dict[str, Any]], Optional[float]]:
        """
        Place a 'bridge' limit order with quantity determined by max loss and swing SL.

        Flow:
            - Fetch price (if not provided)
            - Compute swing SL using get_swing_sl
            - Compute quantity based on MAX_LOSS_PER_BRIDGE
            - Place LIMIT order (ROS mode: no SL/TP)
            - Wait until filled (or timeout)
            - Place SL order at swing SL
        """
        try:
            # Get current price if not provided
            if price is None:
                price = self.get_future_symbol_price(symbol=symbol)

            # Special leverage handling for BTC
            future_leverage = 10 if symbol == "BTCUSDT" else leverage

            if self.mongo_handler is None:
                self.send_alerts(
                    data=None,
                    description=f"MongoHandler not available; cannot place bridge order for {symbol}",
                    fields=None,
                )
                return None, None

            existing_data = self.mongo_handler.get_mongo_historical_data(
                symbol, interval="15m"
            )

            # Swing stop-loss
            if side == "BUY":
                sl_price = get_swing_sl(df=existing_data, n=5, buy_price=price)
            else:
                sl_price = get_swing_sl(df=existing_data, n=5, sell_price=price)

            sl_percent = float(risk_management.get("stop_loss_percent", 0))

            if sl_price is None:
                self.send_alerts(
                    data=None,
                    description=f"No swing level found for {symbol}. Falling back to percent SL.",
                    fields={"symbol": symbol, "price": price, "side": side},
                )
                if side == "BUY":
                    sl_price = round(
                        price * (1.0 - sl_percent / 100.0),
                        1,
                    )
                else:
                    sl_price = round(
                        price * (1.0 + sl_percent / 100.0),
                        1,
                    )

            sl_price = round(sl_price, 1)
            logger.info(f"Stop Loss Price for {symbol}: {sl_price}")

            price_diff = abs(sl_price - price)
            if price_diff <= 0:
                logger.error(
                    f"Computed price_diff <= 0 for bridge order: price={price}, sl_price={sl_price}"
                )
                return None, None

            # Compute quantity based on MAX_LOSS and leverage
            quantity = (self.MAX_LOSS_PER_BRIDGE / future_leverage) / price_diff

            logger.info(
                f"Bridge order quantity for {symbol}: {quantity}, "
                f"MAX_LOSS={self.MAX_LOSS_PER_BRIDGE}, "
                f"price_diff={price_diff}, leverage={future_leverage}"
            )

            # Place base LIMIT order in ROS mode (no SL/TP yet)
            logger.info(f"Placing {side} bridge order for {symbol}...")
            order_response, used_qty, order_request = self.place_order(
                risk_management=risk_management,
                symbol=symbol,
                side=side,
                price=price,
                leverage=future_leverage,
                quantity=quantity,
                ros=True,  # no SL/TP from place_order
            )

            if not order_response:
                logger.error(f"Bridge order failed for {symbol}")
                return None, None

            order_id = order_response["orderId"]
            start_time = time.time()
            timeout = 300  # 5 minutes

            # Wait until order is filled or timeout
            while True:
                order_status = self.future_client.get_orders(
                    symbol=symbol, orderId=order_id
                )

                # Depending on client, this might be list or dict.
                # Handle both possibilities defensively.
                status: Optional[str] = None
                if isinstance(order_status, dict):
                    status = order_status.get("status")
                elif isinstance(order_status, list) and order_status:
                    status = order_status[0].get("status")

                if status == "FILLED":
                    logger.info(
                        f"Bridge order filled for {symbol}, placing SL order at {sl_price}"
                    )
                    break

                elapsed_time = time.time() - start_time
                if elapsed_time > timeout:
                    logger.info(
                        f"Timeout reached while waiting for bridge order fill for {symbol}. "
                        f"OrderId={order_id}"
                    )
                    break

                time.sleep(2)

            # After fill (or timeout), place SL on opposite side
            sl_side = self._get_opposite_side(side)
            if used_qty is None:
                used_qty = quantity

            self.place_sl_order(symbol, sl_side, sl_price, used_qty)

            return order_response, used_qty

        except ClientError as error:
            self.clientExceptionHandler(
                symbol=symbol,
                error=error,
                Location="Order Manager -> place_bridge_order",
            )
        except Exception as e:
            self.handle_exception(
                e, context_description="Exception caught at place_bridge_order"
            )

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
        """
        Modify an existing order for a given symbol.

        Args:
            symbol: e.g. "BTCUSDT"
            side: "BUY" or "SELL"
            orderId: ID of the order to modify
            price: new price
            quantity: new quantity
            order_type: optional descriptor (e.g., "SL", "TP") used in notifications
        """
        try:
            precision = self.config["trading_pairs_precision"][symbol]
            quantity = round(float(quantity), precision)

            # Adjust price and quantity based on filters
            price = self.adjust_price_tick(symbol, price)
            quantity = self.adjust_quantity_step(symbol, quantity)

            modified_order = self.future_client.modify_order(
                symbol=symbol,
                side=side,
                orderId=orderId,
                price=price,
                quantity=quantity,
            )

            self.send_active_trades_info(
                data=None,
                description=f"{symbol} {order_type or ''} Modified ",
                fields=modified_order,
            )
            return modified_order if isinstance(modified_order, list) else [modified_order]

        except ClientError as error:
            self.clientExceptionHandler(
                symbol=symbol,
                error=error,
                Location="OrderManager -> modify_order",
            )
        except Exception as e:
            self.handle_exception(
                e,
                context_description="Exception caught while modifying order",
            )

        return []
