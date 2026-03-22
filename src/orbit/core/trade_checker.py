"""
trade_checker
=============

Provides :class:`TradeChecker`, the real-time position monitor that:

* Ensures exactly one active SL (and optionally TP) per symbol, preventing
  the Binance ``-4045`` duplicate-stop-order error.
* Trails or adapts stop-losses according to the configured strategy.
* Maintains a live-price feed via a Binance WebSocket.
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
import json
import logging
import threading
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import redis
import websocket
from binance.error import ClientError

from config import COIN_TRADE_TYPE, TradeType, TRAILING_STOPLOSS
from orbit.utils.utils import get_indian_time
from orbit.core.authentication_manager import AuthenticationManager
from orbit.core.order_manager import OrderManager
from orbit.core.mongo_handler import MongoHandler
from orbit.strategies.strategy_registry import STRATEGY_REGISTRY

logger = logging.getLogger("Orbit")

# ---------------------------------------------------------------------------
# Redis key helpers
# ---------------------------------------------------------------------------

TRADE_KEY_PREFIX = "trade:"
ORDER_KEY_PREFIX = "order:"


def _trade_key(trade_id: str) -> str:
    return f"{TRADE_KEY_PREFIX}{trade_id}"


def _order_key(order_id: str) -> str:
    return f"{ORDER_KEY_PREFIX}{order_id}"


def is_stop_order(order: Optional[Dict[str, Any]]) -> bool:
    """Return ``True`` if *order* represents a stop-loss conditional/algo order."""
    if not order:
        return False
    t = str(order.get("algoType", "")).upper()
    ot = str(order.get("orderType", "")).upper()

    if t in ("ALGO", "CONDITIONAL") and "STOP" in ot:
        return True
    if ot in ("STOP", "STOP_MARKET", "STOP_LOSS", "STOP_LOSS_LIMIT"):
        return True
    if t in ("STOP", "STOP_MARKET"):
        return True
    return False


def is_take_profit_order(order: Optional[Dict[str, Any]]) -> bool:
    """Return ``True`` if *order* represents a take-profit conditional/algo order."""
    if not order:
        return False
    t = str(order.get("algoType", "")).upper()
    ot = str(order.get("orderType", "")).upper()

    if t in ("ALGO", "CONDITIONAL") and "TAKE_PROFIT" in ot:
        return True
    if ot in ("TAKE_PROFIT", "TAKE_PROFIT_MARKET"):
        return True
    return False


def _extract_trigger_price(order: Optional[Dict[str, Any]]) -> Optional[float]:
    """Extract the trigger/stop price from an order dict.

    Tries the following keys in order:
    ``triggerPrice``, ``stopPrice``, ``stop_price``.

    Returns:
        The price as a ``float``, or ``None`` if not found / not parseable.
    """
    if not order:
        return None
    for key in ("triggerPrice", "stopPrice", "stop_price"):
        val = order.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return None


class TradeChecker(AuthenticationManager):
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

    Args:
        order_manager: Pre-built :class:`OrderManager`.  A new instance is
            created when ``None``.
        mongo_handler: Pre-built :class:`MongoHandler`.  A new instance is
            created when ``None``.
        redis_client: Pre-built ``redis.StrictRedis`` connection.  A default
            ``localhost:6379/0`` connection is created when ``None``.
        **auth_kwargs: Forwarded to :class:`AuthenticationManager`.
    """

    def __init__(
        self,
        order_manager: Optional[OrderManager] = None,
        mongo_handler: Optional[MongoHandler] = None,
        redis_client: Optional[redis.StrictRedis] = None,
        **auth_kwargs: Any,
    ) -> None:
        super().__init__(**auth_kwargs)

        self.cooldown_tracker: Dict[str, str] = {}
        self.trades: Dict[str, Dict[str, Any]] = {}
        self.om: OrderManager = order_manager or OrderManager()
        self.mongo_handler: MongoHandler = mongo_handler or MongoHandler()
        self.live_prices: Dict[str, Tuple[float, float]] = {}
        self.isWebSocketRunning: bool = False
        self.client: redis.StrictRedis = redis_client or redis.StrictRedis(
            host="localhost", port=6379, db=0, decode_responses=True
        )

    # ------------------------------------------------------------------
    # Redis mapping helpers
    # ------------------------------------------------------------------

    def _save_trade(self, trade_id: str, trade: Dict[str, Any]) -> None:
        """Persist *trade* under ``trade:{trade_id}`` in Redis."""
        try:
            self.client.set(_trade_key(trade_id), json.dumps(trade))
        except Exception as e:
            self.handle_exception(e, context_description=f"_save_trade({trade_id})")

    def _load_trade(self, trade_id: str) -> Optional[Dict[str, Any]]:
        """Load and deserialise the trade stored at ``trade:{trade_id}``."""
        try:
            raw = self.client.get(_trade_key(trade_id))
            if raw:
                return json.loads(raw)
        except Exception as e:
            self.handle_exception(e, context_description=f"_load_trade({trade_id})")
        return None

    def _register_order(self, order_id: str, trade_id: str) -> None:
        """Map ``order:{order_id}`` → *trade_id* in Redis."""
        try:
            self.client.set(_order_key(order_id), trade_id)
        except Exception as e:
            self.handle_exception(e, context_description=f"_register_order({order_id})")

    def _trade_id_for_order(self, order_id: str) -> Optional[str]:
        """Return the *trade_id* that owns *order_id*, or ``None``."""
        try:
            return self.client.get(_order_key(order_id))
        except Exception as e:
            self.handle_exception(e, context_description=f"_trade_id_for_order({order_id})")
        return None

    def _delete_trade_mapping(self, trade_id: str) -> None:
        """Remove ``trade:{trade_id}`` and all associated ``order:*`` keys.

        The trade dict is expected to carry ``sl_order_id`` and
        ``tp_order_id`` fields that were written when the orders were placed.
        """
        try:
            trade = self._load_trade(trade_id)
            if trade:
                for field in ("sl_order_id", "tp_order_id"):
                    oid = trade.get(field)
                    if oid:
                        self.client.delete(_order_key(str(oid)))
            self.client.delete(_trade_key(trade_id))
        except Exception as e:
            self.handle_exception(e, context_description=f"_delete_trade_mapping({trade_id})")

    def _update_trade_field(self, trade_id: str, updates: Dict[str, Any]) -> None:
        """Merge *updates* into the persisted trade and re-save."""
        trade = self._load_trade(trade_id) or {}
        trade.update(updates)
        self._save_trade(trade_id, trade)

    # ------------------------------------------------------------------
    # Cooldown helpers
    # ------------------------------------------------------------------

    def is_in_cooldown(self, symbol: str) -> bool:
        """Return ``True`` if *symbol* is still within its cooldown window."""
        cooldown_end = self.client.get(symbol)
        if cooldown_end:
            try:
                ind = get_indian_time()
                return ind.now() < ind.fromisoformat(cooldown_end)
            except Exception:
                return False
        return False

    def set_cooldown(self, symbol: str) -> None:
        """Set a cooldown window for *symbol* in Redis.

        The duration is read from ``config["cooldown_hours"]``; when the
        symbol is not configured a 5-minute default is used.
        """
        cooldown_hours = int(self.config.get("cooldown_hours", {}).get(symbol, 0))
        minutes = 0
        if cooldown_hours == 0:
            minutes = 5
        ind_time = get_indian_time()
        cooldown_end = (ind_time.now() + timedelta(hours=cooldown_hours, minutes=minutes)).isoformat()
        self.client.set(symbol, cooldown_end)

    # ------------------------------------------------------------------
    # Price helpers
    # ------------------------------------------------------------------

    def check_price_freshness(self, symbol: str) -> Optional[float]:
        """Return a fresh price for *symbol*, falling back to the REST API.

        If the cached WebSocket price is older than 2 seconds it is
        refreshed via :meth:`get_future_symbol_price`.

        Returns:
            The current price, or ``None`` when no price can be obtained.
        """
        if symbol in self.live_prices:
            current_price, last_updated = self.live_prices.get(symbol)
            if time.time() - last_updated > 2:
                logger.warning(f"[WARN] Price for {symbol} is stale ({time.time() - last_updated:.2f}s old)")
                try:
                    current_price = self.get_future_symbol_price(symbol=symbol)
                    self.live_prices[symbol] = (current_price, time.time())
                except Exception:
                    pass
            return current_price

        logger.warning(f"[WARN] Live price for {symbol} not found.")
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
        """Guarantee that exactly one SL (and optionally one TP) exists.

        Uses the Redis mapping as the source of truth:

        1. Derive the ``trade_id`` from the trade dict.
        2. Load the persisted trade to discover known order IDs.
        3. Verify each order is still open on the broker.
        4. Re-create any missing orders and update both mappings.

        When an SL or TP order is not found among open orders, the persisted
        ``sl_order`` / ``tp_order`` snapshots stored in Redis are consulted
        first.  Their ``triggerPrice`` is used as the source of truth for
        reconstructing the price level, avoiding a full recalculation from
        the entry price whenever possible.

        Args:
            symbol: Trading pair.
            trade: Position metadata (must contain ``positionSide``,
                ``quantity``, ``price``).
            risk_management: Config dict with ``"stop_loss_percent"`` etc.

        Returns:
            A ``(stop_loss_order, take_profit_order)`` tuple.  Either element
            may be ``None`` if placement failed.
        """
        try:
            trade_id = trade.get("trade_id") or symbol
            persisted = self._load_trade(trade_id) or {}

            # ---- fetch live open orders from broker ----
            open_orders = self.om.get_conditional_open_orders(symbol=symbol)
            open_order_ids = {str(o.get("algoId", "")) for o in open_orders}

            stop_loss_order: Optional[Dict[str, Any]] = None
            take_profit_order: Optional[Dict[str, Any]] = None

            # Match broker orders to our mapping
            for order in open_orders:
                if is_stop_order(order):
                    stop_loss_order = stop_loss_order or order
                if is_take_profit_order(order):
                    take_profit_order = take_profit_order or order

            # ------------------------------------------------------------------
            # Validate persisted SL order ID against live broker state.
            # When the SL is missing from open orders, fall back to the
            # persisted order snapshot to recover the trigger price.
            # ------------------------------------------------------------------
            persisted_sl_id = str(persisted.get("sl_order_id", ""))
            persisted_sl_order: Optional[Dict[str, Any]] = persisted.get("sl_order")

            if persisted_sl_id and persisted_sl_id not in open_order_ids:
                logger.warning(
                    f"[SELF-HEAL] SL order {persisted_sl_id} for {symbol} not found on broker – will recreate"
                )
                stop_loss_order = None  # force recreation

            # ------------------------------------------------------------------
            # Validate persisted TP order ID against live broker state.
            # When the TP is missing from open orders, fall back to the
            # persisted order snapshot to recover the trigger price.
            # ------------------------------------------------------------------
            persisted_tp_id = str(persisted.get("tp_order_id", ""))
            persisted_tp_order: Optional[Dict[str, Any]] = persisted.get("tp_order")

            if persisted_tp_id and persisted_tp_id not in open_order_ids:
                logger.warning(
                    f"[SELF-HEAL] TP order {persisted_tp_id} for {symbol} not found on broker – will recreate"
                )
                take_profit_order = None  # force recreation

            # ---- recreate missing SL ----
            if stop_loss_order is None:
                # Prefer the trigger price from the persisted SL order snapshot
                # so we honour the last known SL level rather than recalculating
                # from the entry price (which may differ after trailing updates).
                sl_price = _extract_trigger_price(persisted_sl_order)
                if sl_price is not None:
                    logger.info(
                        f"[SELF-HEAL] Using persisted SL triggerPrice {sl_price} for {symbol}"
                    )
                else:
                    sl_price = self.calculate_sl_price(trade, risk_management)
                    logger.info(
                        f"[SELF-HEAL] No persisted SL triggerPrice for {symbol}; "
                        f"calculated from entry: {sl_price}"
                    )

                stop_loss_order = self.om.place_sl_order(
                    symbol=symbol,
                    side=("SELL" if trade["positionSide"] == "BUY" else "BUY"),
                    stoploss_price=sl_price,
                    quantity=trade["quantity"],
                )
                time.sleep(0.5)
                if stop_loss_order:
                    new_sl_id = str(stop_loss_order.get("algoId", ""))
                    self._register_order(new_sl_id, trade_id)
                    self._update_trade_field(
                        trade_id,
                        {
                            "sl_order_id": new_sl_id,
                            "sl_order": stop_loss_order,
                        },
                    )
                    # Remove stale mapping for old order id
                    if persisted_sl_id and persisted_sl_id != new_sl_id:
                        self.client.delete(_order_key(persisted_sl_id))
                    logger.info(
                        f"[SELF-HEAL] Placed missing SL for {symbol} at {sl_price} (order {new_sl_id})"
                    )

            # ---- recreate missing TP ----
            if take_profit_order is None and COIN_TRADE_TYPE[symbol] == TradeType.BRACKET_TRADE:
                # Prefer the trigger price from the persisted TP order snapshot.
                target_price = _extract_trigger_price(persisted_tp_order)
                if target_price is not None:
                    logger.info(
                        f"[SELF-HEAL] Using persisted TP triggerPrice {target_price} for {symbol}"
                    )
                else:
                    target_price = self.calculate_target_price(trade, risk_management)
                    logger.info(
                        f"[SELF-HEAL] No persisted TP triggerPrice for {symbol}; "
                        f"calculated from entry: {target_price}"
                    )

                take_profit_order = self.om.place_target_order(
                    symbol=symbol,
                    side=("SELL" if trade["positionSide"] == "BUY" else "BUY"),
                    target_price=target_price,
                    quantity=trade["quantity"],
                )
                time.sleep(0.5)
                if take_profit_order:
                    new_tp_id = str(take_profit_order.get("algoId", ""))
                    self._register_order(new_tp_id, trade_id)
                    self._update_trade_field(
                        trade_id,
                        {
                            "tp_order_id": new_tp_id,
                            "tp_order": take_profit_order,
                        },
                    )
                    if persisted_tp_id and persisted_tp_id != new_tp_id:
                        self.client.delete(_order_key(persisted_tp_id))
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

    def calculate_sl_price(self, trade: Dict[str, Any], risk_management: Dict[str, Any]) -> float:
        """Compute the initial stop-loss price from the entry and risk config.

        Raises:
            ValueError: If ``stop_loss_percent`` is not positive.
        """
        price = float(trade["price"])
        percent = float(risk_management["stop_loss_percent"])
        if percent <= 0:
            raise ValueError("Stop loss percent must be greater than zero")
        if trade["positionSide"] == "BUY":
            return price - (price * (percent / 100.0))
        else:
            return price + (price * (percent / 100.0))

    def calculate_target_price(self, trade: Dict[str, Any], risk_management: Dict[str, Any]) -> float:
        """Compute the take-profit price from the entry and risk config.

        Raises:
            ValueError: If ``target_percent`` is not positive.
        """
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
        """Refresh the in-memory trade record and Redis mapping for *symbol*.

        Updates high/low watermarks, persists the latest SL/TP order
        references (including full order snapshots used for trigger-price
        fallback), and returns a summary dict suitable for Discord
        notifications.
        """
        try:
            stop_price_val = _extract_trigger_price(stop_loss_order)
            stop_loss = stop_price_val  # already float or None

            target = _extract_trigger_price(take_profit_order)

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

            sl_order_id = str(stop_loss_order.get("algoId", "")) if stop_loss_order else existing_trade.get("sl_order_id", "")
            tp_order_id = str(take_profit_order.get("algoId", "")) if take_profit_order else existing_trade.get("tp_order_id", "")

            self.trades[symbol].update({
                "stop_loss_price": stop_loss,
                "target": target,
                "quantity": trade["quantity"],
                "stop_loss_order": stop_loss_order,
                "take_profit_order": take_profit_order,
                "sl_order_id": sl_order_id,
                "tp_order_id": tp_order_id,
            })

            # Persist updated trade state to Redis mapping, including full
            # order snapshots so trigger prices survive restarts / desyncs.
            trade_id = trade.get("trade_id") or symbol
            persisted_updates: Dict[str, Any] = dict(self.trades[symbol])
            if stop_loss_order:
                persisted_updates["sl_order"] = stop_loss_order
            if take_profit_order:
                persisted_updates["tp_order"] = take_profit_order
            self._save_trade(trade_id, persisted_updates)

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

    def _exit_trade(self, symbol: str, trade_id: str) -> None:
        """Remove a trade from in-memory state and clean up Redis mappings.

        Deletes:
        * ``trade:{trade_id}``
        * ``order:{sl_order_id}``
        * ``order:{tp_order_id}``
        """
        self._delete_trade_mapping(trade_id)
        self.trades.pop(symbol, None)
        logger.info(f"[EXIT] Trade {trade_id} for {symbol} removed from state and Redis mappings.")

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
        """Exit the position when price crosses the SMA (adaptive mode).

        Returns:
            ``True`` if the position was closed, ``False`` otherwise.
        """
        historical_df = self.om.mongo_handler.get_mongo_historical_data(symbol, interval="15m")
        if historical_df is None or historical_df.empty:
            return False

        historical_df["timestamp"] = pd.to_datetime(historical_df["timestamp"], unit='ns')
        historical_df = historical_df.set_index("timestamp")

        close = historical_df['close'].copy()
        close.loc[len(close)] = current_price
        sma_series = self.compute_sma(close)
        current_sma = sma_series.iloc[-1]

        self.send_active_trade_prices(
            data=None,
            description=f'{symbol} Adaptive running stats on {side} side',
            fields={"sma": current_sma, "current_price": current_price}
        )

        if side == "BUY" and current_price > current_sma:
            resp = self.om.place_market_order(symbol, 'SELL', quantity)
            if resp:
                trade_id = self.trades.get(symbol, {}).get("trade_id") or symbol
                self._exit_trade(symbol, trade_id)
                return True
        if side == "SELL" and current_price < current_sma:
            resp = self.om.place_market_order(symbol, 'BUY', quantity)
            if resp:
                trade_id = self.trades.get(symbol, {}).get("trade_id") or symbol
                self._exit_trade(symbol, trade_id)
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
        """Atomically replace the current SL with a new one.

        The new SL is placed **first**; the old order is cancelled only
        after the new one is confirmed.  Both Redis mappings are updated,
        including the full order snapshot (``sl_order``) used for
        trigger-price fallback on restart / desync.
        """
        try:
            trade_id = self.trades.get(symbol, {}).get("trade_id") or symbol

            placed = self.om.place_sl_order(symbol, side, new_sl, quantity)
            time.sleep(0.5)

            if placed:
                new_sl_id = str(placed.get("algoId", ""))

                # Cancel old SL order on broker
                if current_stop_order and current_stop_order.get("algoId"):
                    old_sl_id = str(current_stop_order["algoId"])
                    try:
                        self.om.cancel_algo_conditional_order(symbol=symbol, algo_id=old_sl_id)
                    except Exception as e:
                        self.handle_exception(e, f"Failed to cancel old SL order for {symbol} after placing new SL")
                    # Remove stale order mapping
                    self.client.delete(_order_key(old_sl_id))

                # Register new order mapping and persist full order snapshot
                self._register_order(new_sl_id, trade_id)
                self._update_trade_field(
                    trade_id,
                    {
                        "sl_order_id": new_sl_id,
                        "stop_loss_price": new_sl,
                        "sl_order": placed,
                    },
                )

            # Re-fetch to confirm
            orders = self.om.get_conditional_open_orders(symbol=symbol)
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
            self.set_cooldown(symbol)
            trade_id = self.trades[symbol].get("trade_id") or symbol

            if current_price <= stop_loss and stop_loss <= self.trades[symbol]["price"]:
                logger.info(f"Stop-loss hit for {symbol}, Exiting trade.")
                self.send_false_alarm(data=None, description=f"{symbol} SL Hit at BUY side", fields=self.trades[symbol])
                self._exit_trade(symbol, trade_id)
                return

            if current_price <= stop_loss and stop_loss > self.trades[symbol]["price"]:
                logger.info(f"Average hit for {symbol}. Exiting trade.")
                self.send_average_alarm(data=None, description=f"{symbol} SL Hit at BUY side", fields=self.trades[symbol])
                self._exit_trade(symbol, trade_id)
                return

            if COIN_TRADE_TYPE[symbol] == TradeType.BRACKET_TRADE and current_price >= target:
                logger.info(f"Target hit for {symbol}. Exiting trade.")
                self.send_true_alarm(data=None, description=f"{symbol} Target Hit at BUY side", fields=self.trades[symbol])
                self._exit_trade(symbol, trade_id)
                return

            if COIN_TRADE_TYPE[symbol] == TradeType.ADAPTIVE_TRADE:
                if self.adaptive_trade_check(symbol, current_price, "BUY", quantity):
                    return

                cost_price = self.trades[symbol]["price"]
                sl_price = self.trades[symbol].get("stop_loss_price")

                if current_price >= cost_price * 1.03 and sl_price is not None and sl_price < cost_price:
                    new_stop = cost_price
                    new_stop_order = self._place_and_replace_sl(symbol, new_stop, stop_loss_order, quantity, "SELL")
                    if new_stop_order:
                        self.trades[symbol]["stop_loss_price"] = new_stop
                        self.send_sl_update_notifier(data=None, description=f"{symbol}: SL moved to Entry Price", fields={"new_sl": new_stop})
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
            new_stop_loss = signal['stop_loss']

            if new_stop_loss > stop_loss:
                new_stop_order = self._place_and_replace_sl(symbol, new_stop_loss, stop_loss_order, quantity, "SELL")
                if new_stop_order:
                    self.trades[symbol]["stop_loss_price"] = new_stop_loss
                    stop_loss_order = new_stop_order
                    logger.info(f"Updated SL for {symbol} to {new_stop_loss} at price {current_price}")

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
            self.set_cooldown(symbol)
            trade_id = self.trades[symbol].get("trade_id") or symbol

            if current_price >= stop_loss and stop_loss >= self.trades[symbol]["price"]:
                logger.info(f"Stop-loss hit for {symbol}. Exiting trade.")
                self.send_false_alarm(data=None, description=f"{symbol} SL Hit at SELL Side", fields=self.trades[symbol])
                self._exit_trade(symbol, trade_id)
                return

            if current_price >= stop_loss and stop_loss < self.trades[symbol]["price"]:
                logger.info(f"Average hit for {symbol}, Exiting trade.")
                self.send_average_alarm(data=None, description=f"{symbol} SL Hit at SELL Side", fields=self.trades[symbol])
                self._exit_trade(symbol, trade_id)
                return

            if COIN_TRADE_TYPE[symbol] == TradeType.BRACKET_TRADE and current_price <= target:
                logger.info(f"Target hit for {symbol}. Exiting trade.")
                self.send_true_alarm(data=None, description=f"{symbol} Target Hit at SELL Side", fields=self.trades[symbol])
                self._exit_trade(symbol, trade_id)
                return

            if COIN_TRADE_TYPE[symbol] == TradeType.ADAPTIVE_TRADE:
                if self.adaptive_trade_check(symbol, current_price, "SELL", quantity):
                    return

                cost_price = self.trades[symbol]["price"]
                sl_price = self.trades[symbol].get("stop_loss_price")

                if current_price <= cost_price * 0.97 and sl_price is not None and sl_price > cost_price:
                    new_stop = cost_price
                    new_stop_order = self._place_and_replace_sl(symbol, new_stop, stop_loss_order, quantity, "BUY")
                    if new_stop_order:
                        self.trades[symbol]["stop_loss_price"] = new_stop
                        self.send_sl_update_notifier(data=None, description=f"{symbol}: SL moved to Entry Price", fields={"new_sl": new_stop})
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
            new_stop_loss = signal['stop_loss']

            if new_stop_loss < stop_loss:
                new_stop_order = self._place_and_replace_sl(symbol, new_stop_loss, stop_loss_order, quantity, "SELL")
                if new_stop_order:
                    self.trades[symbol]["stop_loss_price"] = new_stop_loss
                    stop_loss_order = new_stop_order
                    logger.info(f"Updated SL for {symbol} to {new_stop_loss} at price {current_price}")

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
            logger.warning(f"[WARN] Current price for {symbol} is invalid, skipping trade check.")
            return

        try:
            self.send_active_trade_prices(data=None, description=f'Price Updates for {symbol}', fields={
                'Entry price': f'{self.trades[symbol].get("price")}',
                'current_price': f'{current_price}',
                'stop_loss': f'{stop_loss}',
                'target': f'{target}',
                'side': f'{self.trades[symbol].get("positionSide")}'
            })

            if self.trades[symbol]["positionSide"] == "BUY":
                self.long_check_trade(risk_management, symbol, stop_loss, target, current_price, stop_loss_order, quantity)
            else:
                self.short_check_trade(risk_management, symbol, stop_loss, target, current_price, stop_loss_order, quantity)

        except Exception as e:
            self.handle_exception(e, context_description="check_trade")

    # ------------------------------------------------------------------
    # Active-position discovery
    # ------------------------------------------------------------------

    def activePosition_coolMaker(self) -> Dict[str, Dict[str, Any]]:
        """Discover all active Futures positions and set cooldowns.

        Reconciles broker positions against Redis mappings:
        * Positions present on the broker are upserted into Redis.
        * Stale Redis trade entries (no matching broker position) are cleaned up.

        Returns:
            A dict mapping each symbol with a non-zero entry price to its
            position metadata.
        """
        positions = self.future_client.get_position_risk()
        trades: Dict[str, Dict[str, Any]] = {}

        active_symbols = set()
        for position in positions:
            entry_price = float(position.get("entryPrice", 0) or 0)
            if entry_price == 0:
                continue

            self.set_cooldown(position["symbol"])
            positionAmount = float(position.get("positionAmt", 0) or 0)
            symbol = position["symbol"]
            active_symbols.add(symbol)

            # Use symbol as trade_id for simplicity (one position per symbol)
            trade_id = symbol
            _dict = {
                "trade_id": trade_id,
                "symbol": symbol,
                "positionSide": "BUY" if positionAmount > 0 else "SELL",
                "quantity": abs(positionAmount),
                "price": entry_price,
            }

            # Merge any persisted fields (e.g. sl_order_id, tp_order_id,
            # sl_order snapshot, tp_order snapshot for trigger-price fallback)
            persisted = self._load_trade(trade_id)
            if persisted:
                for key in (
                    "sl_order_id",
                    "tp_order_id",
                    "sl_order",
                    "tp_order",
                    "high",
                    "low",
                    "stop_loss_price",
                    "target",
                ):
                    if key in persisted:
                        _dict.setdefault(key, persisted[key])

            trades[symbol] = _dict

        # Clean up stale Redis entries for symbols no longer active on broker
        for key in self.client.scan_iter(f"{TRADE_KEY_PREFIX}*"):
            trade_id = key[len(TRADE_KEY_PREFIX):]
            if trade_id not in active_symbols:
                logger.info(f"[CLEANUP] Removing stale Redis trade mapping for trade_id={trade_id}")
                self._delete_trade_mapping(trade_id)

        return trades

    # ------------------------------------------------------------------
    # WebSocket helpers
    # ------------------------------------------------------------------

    def public_websocket(self, trading_pairs: List[str]) -> None:
        """Open a Binance Futures WebSocket for real-time trade prices."""
        def on_message(ws, message):
            try:
                message = json.loads(message)
                if 'data' in message and 's' in message['data'] and 'p' in message['data']:
                    symbol = message['data']['s']
                    price = float(message['data']['p'])
                    self.live_prices[symbol] = (price, time.time())
                else:
                    logger.error(f"Unexpected message format: {message}")
                    self.send_websocket_logs(data='Unexpected message format in Websocket', description=f"{message}", fields=None)
            except Exception:
                logger.exception("Error parsing websocket message")

        def on_error(ws, error):
            logger.error(f"WebSocket error: {error}")
            self.send_websocket_logs(data='Websocket Error', description=f"{error}", fields=None)
            self.isWebSocketRunning = False
            try:
                ws.close()
            except Exception:
                pass

        def on_close(ws, *args):
            logger.info("WebSocket closed")
            self.send_websocket_logs(data='WebSocket closed', description=f"{args}", fields=None)
            self.isWebSocketRunning = False
            try:
                ws.close()
            except Exception:
                pass

        def on_open(ws):
            logger.info("WebSocket connection opened")
            self.send_websocket_logs(data=None, description='WebSocket Connection opened', fields=None)
            self.isWebSocketRunning = True

        streams = "/".join([f"{symbol.lower()}@trade" for symbol in trading_pairs])
        future_socket_url = f"wss://fstream.binance.com/stream?streams={streams}"

        self.future_ws = websocket.WebSocketApp(
            future_socket_url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )

        self.wst = threading.Thread(target=self.future_ws.run_forever, daemon=True)
        self.wst.start()

    def stop_future_ws(self) -> None:
        """Gracefully close the Futures WebSocket connection (if open)."""
        if hasattr(self, 'future_ws'):
            logger.info("Closing Futures WebSocket connection...")
            try:
                self.future_ws.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Main monitor loop
    # ------------------------------------------------------------------

    def monitor_trades(self, trading_pairs: List[str], risk_management: Dict[str, Any]) -> None:
        """Infinite loop that monitors all active positions.

        Designed to run in a dedicated daemon thread.
        """
        last_minute_used = -1
        while True:
            try:
                flag = False
                for symbol in trading_pairs:
                    if symbol not in self.trades:
                        continue

                    if not self.isWebSocketRunning:
                        self.public_websocket(trading_pairs)
                        time.sleep(1)

                    current_price = self.check_price_freshness(symbol)
                    if current_price is None:
                        continue

                    if (
                        'stop_loss_price' not in self.trades[symbol]
                        or 'target' not in self.trades[symbol]
                        or 'stop_loss_order' not in self.trades[symbol]
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
                        self.trades[symbol]["quantity"]
                    )

                if flag or (get_indian_time().minute % 5 == 0 and last_minute_used != get_indian_time().minute):
                    tradesFound = self.activePosition_coolMaker()
                    any_trade_active = False

                    for symbol in trading_pairs:
                        if symbol not in tradesFound and symbol not in self.trades:
                            continue
                        elif symbol not in tradesFound and symbol in self.trades:
                            # Broker no longer shows this position – clean up
                            trade_id = self.trades[symbol].get("trade_id") or symbol
                            self._exit_trade(symbol, trade_id)
                            continue

                        any_trade_active = True
                        current_price = self.check_price_freshness(symbol)
                        if current_price is None:
                            continue

                        stop_loss_order, take_profit_order = self.ensure_orders(symbol, tradesFound[symbol], risk_management)
                        field_params = self.update_trade_data(symbol, tradesFound[symbol], current_price, stop_loss_order, take_profit_order)

                        self.send_active_trades_info(data=None, description=f"{symbol} trade is Active", fields=field_params)
                        time.sleep(2)

                    last_minute_used = get_indian_time().minute

                    if not any_trade_active:
                        self.send_active_trades_info(data=None, description="No trade is Active", fields=None)
                        self.stop_future_ws()

            except ClientError as error:
                self.clientExceptionHandler(symbol=locals().get('symbol', None), error=error, Location="TradeChecker")
            except Exception as e:
                self.handle_exception(e, context_description="Exception in Monitor trade")

            time.sleep(10)
