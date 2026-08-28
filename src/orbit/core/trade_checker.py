"""
trade_checker
=============

Provides :class:`TradeChecker`, the real-time position monitor that:

* Ensures exactly one active SL (and optionally TP) per symbol, preventing
  the Binance ``-4045`` duplicate-stop-order error.
* Trails or adapts stop-losses according to the configured strategy.
* Maintains a live-price feed via a :class:`BinanceWSManager` WebSocket.
* Uses Redis mappings as the single source of truth for all trade state.

Redis key schema
----------------
``trade:{trade_id}``   – full trade data (hash)
``order:{order_id}``   – trade_id (string)

Module-level helpers :func:`is_stop_order` and :func:`is_take_profit_order`
classify algo/conditional orders returned by the Binance API.

Dependencies (:class:`OrderManager`, :class:`MongoHandler`, Redis) can be
**injected** through the constructor.
"""

import time
import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import redis
from binance.error import ClientError

from config import COIN_TRADE_TYPE, TradeType, TRAILING_STOPLOSS
from orbit.utils.utils import get_indian_time
from orbit.core.authentication_manager import AuthenticationManager
from orbit.core.order_manager import OrderManager
from orbit.core.mongo_handler import MongoHandler
from orbit.core.binance_ws_manager import BinanceWSManager
from orbit.core.redis_manager import RedisManager, TRADE_KEY_PREFIX
from orbit.strategies.strategy_registry import STRATEGY_REGISTRY

logger = logging.getLogger("Orbit")

_POSITION_RISK_MAX_ATTEMPTS = 3
_POSITION_RISK_RETRY_DELAY = 1.0


def _order_types(order: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    if not order:
        return "", ""
    return (
        str(order.get("algoType", "")).upper(),
        str(order.get("orderType", "")).upper(),
    )


def is_stop_order(order: Optional[Dict[str, Any]]) -> bool:
    """Return whether *order* is a stop-loss conditional/algo order."""
    algo_type, order_type = _order_types(order)
    return (
        (algo_type in {"ALGO", "CONDITIONAL"} and "STOP" in order_type)
        or order_type in {"STOP", "STOP_MARKET", "STOP_LOSS", "STOP_LOSS_LIMIT"}
        or algo_type in {"STOP", "STOP_MARKET"}
    )


def is_take_profit_order(order: Optional[Dict[str, Any]]) -> bool:
    """Return ``True`` if *order* represents a take-profit conditional/algo order."""
    algo_type, order_type = _order_types(order)
    return (
        algo_type in {"ALGO", "CONDITIONAL"} and "TAKE_PROFIT" in order_type
    ) or order_type in {"TAKE_PROFIT", "TAKE_PROFIT_MARKET"}


class TradeChecker(AuthenticationManager, RedisManager):
    """Real-time position monitor and SL/TP lifecycle manager.

    All trade state is persisted in Redis using two mapping families:

    * ``trade:{trade_id}``  – serialised trade dict (JSON string stored as a
      plain Redis string so the full nested structure is preserved).
    * ``order:{order_id}``  – the ``trade_id`` that owns this order.

    The checker runs in its own thread (see :meth:`monitor_trades`) and
    periodically:

    1. Discovers active positions via the Binance API.
    2. Ensures each position has exactly one SL (and optionally one TP).
    3. Trails or adapts the stop-loss using the symbol's registered strategy.
    4. Sends Discord notifications for every significant event.

    A :class:`BinanceWSManager` is used for the live price feed, providing
    automatic reconnection, ping/pong keepalive, and stale-connection
    detection.

    Args:
        order_manager: Pre-built :class:`OrderManager`.  A new instance is
            created when ``None``.
        mongo_handler: Pre-built :class:`MongoHandler`.  A new instance is
            created when ``None``.
        redis_client: Pre-built ``redis.StrictRedis`` connection.  A default
            ``localhost:6379/0`` connection is created when ``None``.
        ws_stale_threshold: Seconds without a WebSocket message before the
            connection is considered stale and forcibly restarted.
        **auth_kwargs: Forwarded to :class:`AuthenticationManager`.
    """

    def __init__(
        self,
        order_manager: Optional[OrderManager] = None,
        mongo_handler: Optional[MongoHandler] = None,
        redis_client: Optional[redis.StrictRedis] = None,
        ws_stale_threshold: float = 5.0,
        **auth_kwargs: Any,
    ) -> None:
        AuthenticationManager.__init__(self, **auth_kwargs)
        RedisManager.__init__(self, redis_client=redis_client)

        self.cooldown_tracker: Dict[str, str] = {}
        self.trades: Dict[str, Dict[str, Any]] = {}
        self.order_manager: OrderManager = order_manager or OrderManager()
        self.mongo_handler: MongoHandler = mongo_handler or MongoHandler()
        self.live_prices: Dict[str, Tuple[float, float]] = {}
        self._ws_stale_threshold = ws_stale_threshold
        self._ws_manager: Optional[BinanceWSManager] = None

    # ------------------------------------------------------------------
    # WebSocket price-update callback
    # ------------------------------------------------------------------

    def _handle_price_update(self, symbol: str, price: float, timestamp: float) -> None:
        """Receive a price tick from :class:`BinanceWSManager`."""
        self.live_prices[symbol] = (price, timestamp)

    def _handle_ws_status(self, msg: str) -> None:
        """Forward WebSocket status messages to Discord logs."""
        self.send_websocket_logs(data=None, description=msg, fields=None)

    # ------------------------------------------------------------------
    # WebSocket lifecycle
    # ------------------------------------------------------------------

    def _ensure_ws(self, trading_pairs: List[str]) -> None:
        """Start the WebSocket manager if it is not already running."""
        if self._ws_manager is None:
            self._ws_manager = BinanceWSManager(
                trading_pairs=trading_pairs,
                on_price_update=self._handle_price_update,
                on_status_change=self._handle_ws_status,
                stale_threshold=self._ws_stale_threshold,
            )
            self._ws_manager.start()
            logger.info("[TradeChecker] BinanceWSManager started.")
        elif set(self._ws_manager.trading_pairs) != set(trading_pairs):
            logger.info(
                "[TradeChecker] Trading pairs changed — updating WebSocket subscriptions."
            )
            self._ws_manager.update_pairs(trading_pairs)

    def _stop_ws(self) -> None:
        """Stop the WebSocket manager when there are no active trades."""
        if self._ws_manager is not None:
            self._ws_manager.stop()
            self._ws_manager = None
            logger.info("[TradeChecker] BinanceWSManager stopped (no active trades).")

    @property
    def isWebSocketRunning(self) -> bool:
        """``True`` when the WebSocket manager is connected."""
        return self._ws_manager is not None and self._ws_manager.is_connected

    # ------------------------------------------------------------------
    # Cooldown helpers  (delegates to RedisManager)
    # ------------------------------------------------------------------

    def is_in_cooldown(self, symbol: str) -> bool:
        """Return ``True`` if *symbol* is still within its cooldown window."""
        cooldown_end = self.get_cooldown(symbol)
        if cooldown_end:
            try:
                ind = get_indian_time()
                return ind.now() < ind.fromisoformat(cooldown_end)
            except Exception:
                return False
        return False

    def set_cooldown(self, symbol: str) -> None:
        """Set a cooldown window for *symbol* in Redis."""
        cooldown_hours = int(self.config.get("cooldown_hours", {}).get(symbol, 0))
        minutes = 0
        if cooldown_hours == 0:
            minutes = 5
        ind_time = get_indian_time()
        cooldown_end = (
            ind_time.now() + timedelta(hours=cooldown_hours, minutes=minutes)
        ).isoformat()
        # Use RedisManager helper
        self.redis_set(symbol, cooldown_end)

    # ------------------------------------------------------------------
    # Price helpers
    # ------------------------------------------------------------------

    def check_price_freshness(self, symbol: str) -> Optional[float]:
        """Return a fresh price for *symbol*, falling back to the REST API."""
        if symbol in self.live_prices:
            current_price, last_updated = self.live_prices[symbol]
            if time.time() - last_updated > 2:
                logger.warning(
                    f"[WARN] Price for {symbol} is stale "
                    f"({time.time() - last_updated:.2f}s old) — falling back to REST."
                )
                try:
                    current_price = self.get_future_symbol_price(symbol=symbol)
                    self.live_prices[symbol] = (current_price, time.time())
                except Exception:
                    pass
            return current_price

        logger.warning(f"[WARN] Live price for {symbol} not found — fetching via REST.")
        try:
            current_price = self.get_future_symbol_price(symbol=symbol)
            self.live_prices[symbol] = (current_price, time.time())
            return current_price
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Ensure SL / TP orders  (self-healing via mapping)
    # ------------------------------------------------------------------

    def ensure_orders(
        self,
        symbol: str,
        trade: Dict[str, Any],
        risk_management: Dict[str, Any],
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Guarantee that exactly one SL (and optionally one TP) exists."""
        try:
            trade_id = trade.get("trade_id") or symbol

            open_orders = self.order_manager.get_conditional_open_orders(symbol=symbol)
            open_order_ids = {str(o.get("algoId", "")) for o in open_orders}

            stop_loss_order: Optional[Dict[str, Any]] = None
            take_profit_order: Optional[Dict[str, Any]] = None

            for order in open_orders:
                if is_stop_order(order):
                    stop_loss_order = stop_loss_order or order
                if is_take_profit_order(order):
                    take_profit_order = take_profit_order or order

            if stop_loss_order:
                self.register_order(str(stop_loss_order.get("algoId", "")), trade_id)
                self.update_trade_fields(
                    trade_id, {"sl_order_id": str(stop_loss_order.get("algoId", ""))}
                )
                self.update_trade_fields(trade_id, {"stop_loss_order": stop_loss_order})
                self.update_trade_fields(
                    trade_id,
                    {
                        "stop_loss_price": stop_loss_order.get("stopPrice")
                        or stop_loss_order.get("triggerPrice")
                        or stop_loss_order.get("stop_price")
                    },
                )

            if take_profit_order:
                self.register_order(str(take_profit_order.get("algoId", "")), trade_id)
                self.update_trade_fields(
                    trade_id, {"tp_order_id": str(take_profit_order.get("algoId", ""))}
                )
                self.update_trade_fields(
                    trade_id, {"take_profit_order": take_profit_order}
                )
                self.update_trade_fields(
                    trade_id,
                    {
                        "target": take_profit_order.get("stopPrice")
                        or take_profit_order.get("triggerPrice")
                        or take_profit_order.get("stop_price")
                    },
                )

            persisted = self.load_trade(trade_id) or {}

            persisted_sl_id = str(persisted.get("sl_order_id", ""))
            if persisted_sl_id and persisted_sl_id not in open_order_ids:
                logger.warning(
                    f"[SELF-HEAL] SL order {persisted_sl_id} for {symbol} not found on broker – will recreate"
                )
                stop_loss_order = None

            persisted_tp_id = str(persisted.get("tp_order_id", ""))
            if persisted_tp_id and persisted_tp_id not in open_order_ids:
                logger.warning(
                    f"[SELF-HEAL] TP order {persisted_tp_id} for {symbol} not found on broker – will recreate"
                )
                take_profit_order = None

            if stop_loss_order is None:
                sl_price = persisted.get("stop_loss_price") or self.calculate_sl_price(
                    trade, risk_management
                )
                stop_loss_order = self.order_manager.place_sl_order(
                    symbol=symbol,
                    side=("SELL" if trade["positionSide"] == "BUY" else "BUY"),
                    stoploss_price=sl_price,
                    quantity=trade["quantity"],
                )
                time.sleep(0.5)
                if stop_loss_order:
                    new_sl_id = str(stop_loss_order.get("algoId", ""))
                    self.register_order(new_sl_id, trade_id)
                    self.update_trade_fields(trade_id, {"sl_order_id": new_sl_id})
                    if persisted_sl_id and persisted_sl_id != new_sl_id:
                        self.deregister_order(persisted_sl_id)
                    logger.info(
                        f"[SELF-HEAL] Placed missing SL for {symbol} at {sl_price} (order {new_sl_id})"
                    )

            if (
                take_profit_order is None
                and COIN_TRADE_TYPE[symbol] == TradeType.BRACKET_TRADE
            ):
                target_price = persisted.get("target") or self.calculate_target_price(
                    trade, risk_management
                )
                take_profit_order = self.order_manager.place_target_order(
                    symbol=symbol,
                    side=("SELL" if trade["positionSide"] == "BUY" else "BUY"),
                    target_price=target_price,
                    quantity=trade["quantity"],
                )
                time.sleep(0.5)
                if take_profit_order:
                    new_tp_id = str(take_profit_order.get("algoId", ""))
                    self.register_order(new_tp_id, trade_id)
                    self.update_trade_fields(trade_id, {"tp_order_id": new_tp_id})
                    if persisted_tp_id and persisted_tp_id != new_tp_id:
                        self.deregister_order(persisted_tp_id)
                    logger.info(
                        f"[SELF-HEAL] Placed missing TP for {symbol} at {target_price} (order {new_tp_id})"
                    )

            return stop_loss_order, take_profit_order

        except Exception as e:
            self.handle_exception(e, context_description="ensure_orders")
            return None, None

    # ------------------------------------------------------------------
    # Price / calculation helpers
    # ------------------------------------------------------------------

    def calculate_sl_price(
        self, trade: Dict[str, Any], risk_management: Dict[str, Any]
    ) -> float:
        """Compute the initial stop-loss price from the entry and risk config."""
        price = float(trade["price"])
        percent = float(risk_management["stop_loss_percent"])
        if percent <= 0:
            raise ValueError("Stop loss percent must be greater than zero")
        if trade["positionSide"] == "BUY":
            return price - (price * (percent / 100.0))
        else:
            return price + (price * (percent / 100.0))

    def calculate_target_price(
        self, trade: Dict[str, Any], risk_management: Dict[str, Any]
    ) -> float:
        """Compute the take-profit price from the entry and risk config."""
        price = float(trade["price"])
        percent = float(risk_management.get("target_percent", 2))
        if percent <= 0:
            raise ValueError("Target percent must be greater than zero")
        if trade["positionSide"] == "BUY":
            return price + (price * (percent / 100.0))
        else:
            return price - (price * (percent / 100.0))

    # ------------------------------------------------------------------
    # Trade-data bookkeeping
    # ------------------------------------------------------------------

    def update_trade_data(
        self,
        symbol: str,
        trade: Dict[str, Any],
        current_price: float,
        stop_loss_order: Optional[Dict[str, Any]],
        take_profit_order: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Refresh the in-memory trade record and Redis mapping for *symbol*."""
        try:
            stop_price_val = None
            if stop_loss_order:
                stop_price_val = (
                    stop_loss_order.get("stopPrice")
                    or stop_loss_order.get("triggerPrice")
                    or stop_loss_order.get("stop_price")
                )
            stop_loss = float(stop_price_val) if stop_price_val is not None else None

            target = None
            if take_profit_order:
                tval = (
                    take_profit_order.get("stopPrice")
                    or take_profit_order.get("triggerPrice")
                    or take_profit_order.get("stop_price")
                )
                target = float(tval) if tval is not None else None

            existing_trade = self.trades.get(symbol, {})
            highest_price = existing_trade.get("high")
            lowest_price = existing_trade.get("low")

            if not existing_trade:
                self.trades[symbol] = trade.copy()

            position_side = trade["positionSide"]
            if position_side == "BUY":
                if highest_price is None:
                    high = max(
                        current_price,
                        trade["price"] if trade.get("price") else current_price,
                        stop_loss or current_price,
                    )
                else:
                    high = max(current_price, highest_price)
                self.trades[symbol]["high"] = high
            else:
                if lowest_price is None:
                    low = min(
                        current_price,
                        trade["price"] if trade.get("price") else current_price,
                        stop_loss or current_price,
                    )
                else:
                    low = min(current_price, lowest_price)
                self.trades[symbol]["low"] = low

            sl_order_id = (
                str(stop_loss_order.get("algoId", ""))
                if stop_loss_order
                else existing_trade.get("sl_order_id", "")
            )
            tp_order_id = (
                str(take_profit_order.get("algoId", ""))
                if take_profit_order
                else existing_trade.get("tp_order_id", "")
            )

            self.trades[symbol].update(
                {
                    "stop_loss_price": stop_loss,
                    "target": target,
                    "quantity": trade["quantity"],
                    "stop_loss_order": stop_loss_order,
                    "take_profit_order": take_profit_order,
                    "sl_order_id": sl_order_id,
                    "tp_order_id": tp_order_id,
                }
            )

            trade_id = trade.get("trade_id") or symbol
            self.save_trade(trade_id, self.trades[symbol])

            fields = {
                "symbol": symbol,
                "positionSide": position_side,
                "quantity": trade["quantity"],
                "target": target,
                "cost price": trade.get("price"),
                "current price": current_price,
                "stop loss": stop_loss,
            }

            if position_side == "BUY":
                fields["high"] = self.trades[symbol].get("high")
            else:
                fields["low"] = self.trades[symbol].get("low")

            return fields

        except Exception as e:
            self.handle_exception(e, context_description="update_trade_data")
            return {}

    # ------------------------------------------------------------------
    # Trade exit helper (mapping cleanup)
    # ------------------------------------------------------------------

    def _mark_exit_pending(self, symbol: str, trade_id: str) -> None:
        """Keep a triggered trade protected until reconciliation confirms it is flat."""
        trade = self.trades.get(symbol)
        if trade is not None:
            trade["exit_pending"] = True
        self.update_trade_fields(trade_id, {"exit_pending": True})
        logger.info(
            "[EXIT] Trade %s for %s is pending broker confirmation.",
            trade_id,
            symbol,
        )

    def _cancel_protective_orders(self, symbol: str, trade_id: str) -> None:
        """Best-effort cancel every SL/TP order associated with a trade."""
        persisted = self.load_trade(trade_id) or {}
        trades = getattr(self, "trades", {})
        active_trade = trades.get(symbol, {})
        order_ids = {
            str(order_id)
            for trade in (persisted, active_trade)
            for field in ("sl_order_id", "tp_order_id")
            if (order_id := trade.get(field))
        }

        for order_id in order_ids:
            try:
                self.order_manager.cancel_algo_conditional_order(symbol, order_id)
            except Exception as error:
                # One order may already be terminal because it closed the position.
                # Continue so its still-open sibling always gets a cancellation attempt.
                logger.warning(
                    "[EXIT] Could not cancel protective order %s for %s: %s",
                    order_id,
                    symbol,
                    error,
                )

    def _exit_trade(self, symbol: str, trade_id: str) -> None:
        """Clean up a trade after broker reconciliation confirms it is flat."""
        self._cancel_protective_orders(symbol, trade_id)
        self.delete_trade_with_orders(trade_id)
        getattr(self, "trades", {}).pop(symbol, None)
        self.set_cooldown(symbol)
        logger.info(
            f"[EXIT] Trade {trade_id} for {symbol} removed from state and Redis mappings."
        )

    # ------------------------------------------------------------------
    # Adaptive / trailing logic
    # ------------------------------------------------------------------

    @staticmethod
    def compute_sma(close_series: pd.Series, period: int = 20) -> pd.Series:
        """Return the simple moving average of *close_series*."""
        return close_series.rolling(window=period).mean()

    def adaptive_trade_check(
        self, symbol: str, current_price: float, side: str, quantity: float
    ) -> bool:
        """Exit the position when price crosses the SMA (adaptive mode)."""
        historical_df = self.order_manager.mongo_handler.get_mongo_historical_data(
            symbol, interval="15m"
        )
        if historical_df is None or historical_df.empty:
            return False

        historical_df["timestamp"] = pd.to_datetime(
            historical_df["timestamp"], unit="ns"
        )
        historical_df = historical_df.set_index("timestamp")

        close = historical_df["close"].copy()
        close.loc[len(close)] = current_price
        sma_series = self.compute_sma(close)
        current_sma = sma_series.iloc[-1]

        self.send_active_trade_prices(
            data=None,
            description=f"{symbol} Adaptive running stats on {side} side",
            fields={"sma": current_sma, "current_price": current_price},
        )

        if side == "BUY" and current_price > current_sma:
            resp = self.order_manager.place_market_order(symbol, "SELL", quantity)
            if resp:
                trade_id = self.trades.get(symbol, {}).get("trade_id") or symbol
                self._mark_exit_pending(symbol, trade_id)
                return True
        if side == "SELL" and current_price < current_sma:
            resp = self.order_manager.place_market_order(symbol, "BUY", quantity)
            if resp:
                trade_id = self.trades.get(symbol, {}).get("trade_id") or symbol
                self._mark_exit_pending(symbol, trade_id)
                return True

        return False

    def _place_and_replace_sl(
        self,
        symbol: str,
        new_sl: float,
        current_stop_order: Optional[Dict[str, Any]],
        quantity: float,
        side: str,
    ) -> Optional[Dict[str, Any]]:
        """Atomically replace the current SL with a new one."""
        try:
            trade_id = self.trades.get(symbol, {}).get("trade_id") or symbol

            placed = self.order_manager.place_sl_order(symbol, side, new_sl, quantity)
            time.sleep(0.5)

            if placed:
                new_sl_id = str(placed.get("algoId", ""))

                if current_stop_order and current_stop_order.get("algoId"):
                    old_sl_id = str(current_stop_order["algoId"])
                    try:
                        self.order_manager.cancel_algo_conditional_order(
                            symbol=symbol, algo_id=old_sl_id
                        )
                    except Exception as e:
                        self.handle_exception(
                            e,
                            f"Failed to cancel old SL order for {symbol} after placing new SL",
                        )
                    self.deregister_order(old_sl_id)

                self.register_order(new_sl_id, trade_id)
                self.update_trade_fields(
                    trade_id, {"sl_order_id": new_sl_id, "stop_loss_price": new_sl}
                )

            orders = self.order_manager.get_conditional_open_orders(symbol=symbol)
            for o in orders:
                if is_stop_order(o):
                    return o

            return placed

        except Exception as e:
            self.handle_exception(e, context_description="_place_and_replace_sl")
            return None

    # ------------------------------------------------------------------
    # Long / short position checks
    # ------------------------------------------------------------------

    def long_check_trade(
        self,
        risk_management: Dict[str, Any],
        symbol: str,
        stop_loss: float,
        target: float,
        current_price: float,
        stop_loss_order: Dict[str, Any],
        quantity: float,
    ) -> None:
        """Run all trade-management checks for a **long** position."""
        try:
            trade_id = self.trades[symbol].get("trade_id") or symbol

            if current_price <= stop_loss and stop_loss <= self.trades[symbol]["price"]:
                logger.info(f"Stop-loss hit for {symbol}, Exiting trade.")
                self.send_false_alarm(
                    data=None,
                    description=f"{symbol} SL Hit at BUY side",
                    fields=self.trades[symbol],
                )
                self._mark_exit_pending(symbol, trade_id)
                return

            if current_price <= stop_loss and stop_loss > self.trades[symbol]["price"]:
                logger.info(f"Average hit for {symbol}. Exiting trade.")
                self.send_average_alarm(
                    data=None,
                    description=f"{symbol} SL Hit at BUY side",
                    fields=self.trades[symbol],
                )
                self._mark_exit_pending(symbol, trade_id)
                return

            if (
                COIN_TRADE_TYPE[symbol] == TradeType.BRACKET_TRADE
                and current_price >= target
            ):
                logger.info(f"Target hit for {symbol}. Exiting trade.")
                self.send_true_alarm(
                    data=None,
                    description=f"{symbol} Target Hit at BUY side",
                    fields=self.trades[symbol],
                )
                self._mark_exit_pending(symbol, trade_id)
                return

            if COIN_TRADE_TYPE[symbol] == TradeType.ADAPTIVE_TRADE:
                if self.adaptive_trade_check(symbol, current_price, "BUY", quantity):
                    return

                cost_price = self.trades[symbol]["price"]
                sl_price = self.trades[symbol].get("stop_loss_price")

                if (
                    current_price >= cost_price * 1.03
                    and sl_price is not None
                    and sl_price < cost_price
                ):
                    new_stop = cost_price
                    new_stop_order = self._place_and_replace_sl(
                        symbol, new_stop, stop_loss_order, quantity, "SELL"
                    )
                    if new_stop_order:
                        self.trades[symbol]["stop_loss_price"] = new_stop
                        self.send_sl_update_notifier(
                            data=None,
                            description=f"{symbol}: SL moved to Entry Price",
                            fields={"new_sl": new_stop},
                        )
                    return

            if not TRAILING_STOPLOSS.get(symbol, True):
                return

            strategy_class = STRATEGY_REGISTRY.get(symbol)
            if not strategy_class:
                self.send_alerts(f"No strategy found for {symbol}", None)
                return

            historical_data = self.mongo_handler.handle_mongo_data(symbol)
            strategy = strategy_class(historical_data)
            signal = strategy.generate_signals(symbol=symbol, position_side="LONG")
            new_stop_loss = signal["stop_loss"]

            if new_stop_loss > stop_loss:
                new_stop_order = self._place_and_replace_sl(
                    symbol, new_stop_loss, stop_loss_order, quantity, "SELL"
                )
                if new_stop_order:
                    self.trades[symbol]["stop_loss_price"] = new_stop_loss
                    stop_loss_order = new_stop_order
                    logger.info(
                        f"Updated SL for {symbol} to {new_stop_loss} at price {current_price}"
                    )

        except Exception as e:
            self.handle_exception(e, context_description="long_check_trade")

    def short_check_trade(
        self,
        risk_management: Dict[str, Any],
        symbol: str,
        stop_loss: float,
        target: float,
        current_price: float,
        stop_loss_order: Dict[str, Any],
        quantity: float,
    ) -> None:
        """Run all trade-management checks for a **short** position."""
        try:
            trade_id = self.trades[symbol].get("trade_id") or symbol

            if current_price >= stop_loss and stop_loss >= self.trades[symbol]["price"]:
                logger.info(f"Stop-loss hit for {symbol}. Exiting trade.")
                self.send_false_alarm(
                    data=None,
                    description=f"{symbol} SL Hit at SELL Side",
                    fields=self.trades[symbol],
                )
                self._mark_exit_pending(symbol, trade_id)
                return

            if current_price >= stop_loss and stop_loss < self.trades[symbol]["price"]:
                logger.info(f"Average hit for {symbol}, Exiting trade.")
                self.send_average_alarm(
                    data=None,
                    description=f"{symbol} SL Hit at SELL Side",
                    fields=self.trades[symbol],
                )
                self._mark_exit_pending(symbol, trade_id)
                return

            if (
                COIN_TRADE_TYPE[symbol] == TradeType.BRACKET_TRADE
                and current_price <= target
            ):
                logger.info(f"Target hit for {symbol}. Exiting trade.")
                self.send_true_alarm(
                    data=None,
                    description=f"{symbol} Target Hit at SELL Side",
                    fields=self.trades[symbol],
                )
                self._mark_exit_pending(symbol, trade_id)
                return

            if COIN_TRADE_TYPE[symbol] == TradeType.ADAPTIVE_TRADE:
                if self.adaptive_trade_check(symbol, current_price, "SELL", quantity):
                    return

                cost_price = self.trades[symbol]["price"]
                sl_price = self.trades[symbol].get("stop_loss_price")

                if (
                    current_price <= cost_price * 0.97
                    and sl_price is not None
                    and sl_price > cost_price
                ):
                    new_stop = cost_price
                    new_stop_order = self._place_and_replace_sl(
                        symbol, new_stop, stop_loss_order, quantity, "BUY"
                    )
                    if new_stop_order:
                        self.trades[symbol]["stop_loss_price"] = new_stop
                        self.send_sl_update_notifier(
                            data=None,
                            description=f"{symbol}: SL moved to Entry Price",
                            fields={"new_sl": new_stop},
                        )
                    return

            if not TRAILING_STOPLOSS.get(symbol, True):
                return

            strategy_class = STRATEGY_REGISTRY.get(symbol)
            if not strategy_class:
                self.send_alerts(f"No strategy found for {symbol}", None)
                return

            historical_data = self.mongo_handler.handle_mongo_data(symbol)
            strategy = strategy_class(historical_data)
            signal = strategy.generate_signals(symbol=symbol, position_side="SHORT")
            new_stop_loss = signal["stop_loss"]

            if new_stop_loss < stop_loss:
                new_stop_order = self._place_and_replace_sl(
                    symbol, new_stop_loss, stop_loss_order, quantity, "SELL"
                )
                if new_stop_order:
                    self.trades[symbol]["stop_loss_price"] = new_stop_loss
                    stop_loss_order = new_stop_order
                    logger.info(
                        f"Updated SL for {symbol} to {new_stop_loss} at price {current_price}"
                    )

        except Exception as e:
            self.handle_exception(e, context_description="short_check_trade")

    # ------------------------------------------------------------------
    # Main check wrapper
    # ------------------------------------------------------------------

    def check_trade(
        self,
        risk_management: Dict[str, Any],
        symbol: str,
        stop_loss: float,
        target: float,
        current_price: float,
        stop_loss_order: Dict[str, Any],
        quantity: Optional[float] = None,
    ) -> None:
        """Dispatch to :meth:`long_check_trade` or :meth:`short_check_trade`."""
        if current_price is None or current_price == 0:
            logger.warning(
                f"[WARN] Current price for {symbol} is invalid, skipping trade check."
            )
            return

        if self.trades.get(symbol, {}).get("exit_pending"):
            return

        try:
            self.send_active_trade_prices(
                data=None,
                description=f"Price Updates for {symbol}",
                fields={
                    "Entry price": f'{self.trades[symbol].get("price")}',
                    "current_price": f"{current_price}",
                    "stop_loss": f"{stop_loss}",
                    "target": f"{target}",
                    "side": f'{self.trades[symbol].get("positionSide")}',
                },
            )

            if self.trades[symbol]["positionSide"] == "BUY":
                self.long_check_trade(
                    risk_management,
                    symbol,
                    stop_loss,
                    target,
                    current_price,
                    stop_loss_order,
                    quantity,
                )
            else:
                self.short_check_trade(
                    risk_management,
                    symbol,
                    stop_loss,
                    target,
                    current_price,
                    stop_loss_order,
                    quantity,
                )

        except Exception as e:
            self.handle_exception(e, context_description="check_trade")

    # ------------------------------------------------------------------
    # Active-position discovery
    # ------------------------------------------------------------------

    def _get_position_risk(self, client: Any) -> List[Dict[str, Any]]:
        """Fetch a position snapshot, retrying only transient read timeouts.

        Binance ``-1007`` means the result of a request is unknown.  Retrying
        is safe here because ``get_position_risk`` is a read-only operation;
        order mutations must continue to handle that response as ambiguous.
        """
        delay = _POSITION_RISK_RETRY_DELAY
        for attempt in range(1, _POSITION_RISK_MAX_ATTEMPTS + 1):
            try:
                return client.get_position_risk()
            except ClientError as error:
                is_backend_timeout = (
                    getattr(error, "status_code", None) == 408
                    and getattr(error, "error_code", None) == -1007
                )
                if not is_backend_timeout or attempt == _POSITION_RISK_MAX_ATTEMPTS:
                    raise

                logger.warning(
                    "Position-risk request timed out (attempt %s/%s); retrying in %.1fs",
                    attempt,
                    _POSITION_RISK_MAX_ATTEMPTS,
                    delay,
                )
                time.sleep(delay)
                delay *= 2

        raise RuntimeError("position-risk retry loop exited unexpectedly")

    def activePosition_coolMaker(self) -> Dict[str, Dict[str, Any]]:
        """Discover Futures positions with non-zero broker exposure."""
        clients = {
            id(
                self.order_manager.futures_clients[mode]
            ): self.order_manager.futures_clients[mode]
            for mode in self.order_manager.execution_settings.active_modes
        }
        positions = [
            position
            for client in clients.values()
            for position in self._get_position_risk(client)
        ]
        trades: Dict[str, Dict[str, Any]] = {}

        active_symbols = set()
        for position in positions:
            entry_price = float(position.get("entryPrice", 0) or 0)
            positionAmount = float(position.get("positionAmt", 0) or 0)
            if entry_price == 0 or positionAmount == 0:
                continue

            symbol = position["symbol"]
            active_symbols.add(symbol)

            trade_id = symbol
            _dict = {
                "trade_id": trade_id,
                "symbol": symbol,
                "positionSide": "BUY" if positionAmount > 0 else "SELL",
                "quantity": abs(positionAmount),
                "price": entry_price,
            }

            persisted = self.load_trade(trade_id)
            if persisted:
                for key in (
                    "sl_order_id",
                    "tp_order_id",
                    "high",
                    "low",
                    "stop_loss_price",
                    "target",
                ):
                    if key in persisted:
                        _dict.setdefault(key, persisted[key])

            trades[symbol] = _dict

        # Clean up stale Redis entries for symbols no longer active on broker
        for key in self.scan_trade_keys():
            trade_id = key[len(TRADE_KEY_PREFIX) :]
            persisted = self.load_trade(trade_id) or {}
            symbol = str(persisted.get("symbol") or trade_id)
            if symbol not in active_symbols:
                logger.info(
                    "[CLEANUP] Broker exposure is flat for "
                    f"symbol={symbol}, trade_id={trade_id}; starting cooldown"
                )
                self._exit_trade(symbol, trade_id)

        return trades

    # ------------------------------------------------------------------
    # Main monitor loop
    # ------------------------------------------------------------------

    def monitor_trades(
        self, trading_pairs: List[str], risk_management: Dict[str, Any]
    ) -> None:
        """Infinite loop that monitors all active positions."""
        last_minute_used = -1

        while True:
            try:
                flag = False
                active_trade_symbols = [s for s in trading_pairs if s in self.trades]

                if active_trade_symbols:
                    self._ensure_ws(trading_pairs)
                else:
                    if self._ws_manager is not None:
                        self._stop_ws()

                for symbol in active_trade_symbols:
                    current_price = self.check_price_freshness(symbol)
                    if current_price is None:
                        continue

                    if (
                        "stop_loss_price" not in self.trades[symbol]
                        or "target" not in self.trades[symbol]
                        or "stop_loss_order" not in self.trades[symbol]
                    ):
                        flag = True
                        break

                    self.check_trade(
                        risk_management,
                        symbol,
                        self.trades[symbol]["stop_loss_price"],
                        self.trades[symbol]["target"],
                        current_price,
                        self.trades[symbol]["stop_loss_order"],
                        self.trades[symbol]["quantity"],
                    )
                    if self.trades.get(symbol, {}).get("exit_pending"):
                        flag = True
                        break

                if flag or (
                    get_indian_time().minute % 5 == 0
                    and last_minute_used != get_indian_time().minute
                ):
                    tradesFound = self.activePosition_coolMaker()
                    any_trade_active = False

                    for symbol in trading_pairs:
                        if symbol not in tradesFound and symbol not in self.trades:
                            continue
                        elif symbol not in tradesFound and symbol in self.trades:
                            trade_id = self.trades[symbol].get("trade_id") or symbol
                            self._exit_trade(symbol, trade_id)
                            continue

                        any_trade_active = True
                        current_price = self.check_price_freshness(symbol)
                        if current_price is None:
                            continue

                        stop_loss_order, take_profit_order = self.ensure_orders(
                            symbol, tradesFound[symbol], risk_management
                        )
                        field_params = self.update_trade_data(
                            symbol,
                            tradesFound[symbol],
                            current_price,
                            stop_loss_order,
                            take_profit_order,
                        )

                        self.send_active_trades_info(
                            data=None,
                            description=f"{symbol} trade is Active",
                            fields=field_params,
                        )
                        time.sleep(2)

                    last_minute_used = get_indian_time().minute

                    if not any_trade_active:
                        self.send_active_trades_info(
                            data=None, description="No trade is Active", fields=None
                        )
                        self._stop_ws()

            except ClientError as error:
                self.clientExceptionHandler(
                    symbol=locals().get("symbol", None),
                    error=error,
                    Location="TradeChecker",
                )
            except Exception as e:
                self.handle_exception(
                    e, context_description="Exception in Monitor trade"
                )

            time.sleep(10)
