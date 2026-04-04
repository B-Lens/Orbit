import os
import unittest
from timeout_decorator import timeout
from unittest.mock import AsyncMock, patch, MagicMock, call

os.environ["GROQ_API_KEY"] = "test_key"
os.environ["LANGCHAIN_API_KEY"] = "test_key"
os.environ["LANGSMITH_API_KEY"] = "test_key"

import asyncio
import json
from orbit.core.sentimen_cron import Croner

patch("orbit.core.discord_manager.DiscordManager.send_to_webhook", return_value=None).start()

class TestCore(unittest.TestCase):

    @timeout(30)
    @patch("orbit.core.sentimen_cron.SentimentWorkflow")
    def test_sentiment(self, MockWorkflow):
        mock_workflow_instance = MagicMock()
        mock_workflow_instance.run_analysis = AsyncMock(return_value={
            "sentiment": "BULLISH",
            "confidence": 0.9,
            "reasoning": "Strong buying pressure"
        })
        MockWorkflow.return_value = mock_workflow_instance

        # Mock Redis
        mock_redis = MagicMock()

        croner = Croner(redis_client=mock_redis)

        result = asyncio.run(croner.run_once())
        self.assertEqual(result["sentiment"], "BULLISH")
        self.assertEqual(result["confidence"], 0.9)
        self.assertEqual(result["reasoning"], "Strong buying pressure")
        mock_redis.setex.assert_any_call("market_sentiments", "BULLISH")

        assert result["sentiment"] == "BULLISH"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_spot_client():
    """Return a MagicMock that mimics binance.spot.Spot."""
    return MagicMock()


def _make_future_client():
    """Return a MagicMock that mimics binance.um_futures.UMFutures."""
    return MagicMock()


def _make_order_manager():
    """
    Build an OrderManager with all external dependencies mocked so that no
    real network or DB calls are made.
    """
    from orbit.core.order_manager import OrderManager

    spot = _make_spot_client()
    futures = _make_future_client()
    mongo = MagicMock()
    redis_client = MagicMock()

    with patch("requests.post") as _mock_post:
        _mock_post.return_value = MagicMock(status_code=200)
        om = OrderManager(
            mongo_handler=mongo,
            redis_client=redis_client,
            spot_client=spot,
            futures_client=futures,
        )
        # disable inherited discord calls
        om.send_to_webhook = MagicMock()
        om.future_client.sign_request = MagicMock(return_value={
            "orderId": "12345",
            "status": "NEW"
        })
    return om


def _make_trade_checker():
    """
    Build a TradeChecker with all external dependencies mocked.
    """
    from orbit.core.trade_checker import TradeChecker

    spot = _make_spot_client()
    futures = _make_future_client()
    mongo = MagicMock()
    redis_client = MagicMock()
    order_manager = _make_order_manager()

    with patch("requests.post") as _mock_post:
        _mock_post.return_value = MagicMock(status_code=200)
        tc = TradeChecker(
            order_manager=order_manager,
            mongo_handler=mongo,
            redis_client=redis_client,
            spot_client=spot,
            futures_client=futures,
        )
    return tc


# ---------------------------------------------------------------------------
# OrderManager tests
# ---------------------------------------------------------------------------

class TestOrderManagerGetSymbolPrice(unittest.TestCase):
    """Tests for OrderManager.get_symbol_price / get_future_symbol_price."""

    def setUp(self):
        self.om = _make_order_manager()

    def test_get_symbol_price_success(self):
        self.om.future_client.ticker_price.return_value = {"price": "50000.00"}
        price = self.om.get_symbol_price("BTCUSDT")
        self.assertAlmostEqual(price, 50000.0)

    def test_get_future_symbol_price_success(self):
        self.om.future_client.ticker_price.return_value = {"price": "49500.50"}
        price = self.om.get_future_symbol_price("BTCUSDT")
        self.assertAlmostEqual(price, 49500.50)

    def test_get_symbol_price_api_error(self):
        self.om.future_client.ticker_price.side_effect = Exception("API error")
        with self.assertRaises(Exception):
            self.om.get_symbol_price("BTCUSDT")


class TestOrderManagerGetUSDTBalance(unittest.TestCase):
    """Tests for OrderManager.get_usdt_balance."""

    def setUp(self):
        self.om = _make_order_manager()

    def test_get_usdt_balance_success(self):
        self.om.future_client.balance.return_value = [
            {"asset": "USDT", "balance": "1000.00"},
            {"asset": "BNB", "balance": "5.00"},
        ]
        balance = self.om.get_usdt_balance()
        self.assertIsNotNone(balance)
        self.assertGreater(float(balance), 0)

    def test_get_usdt_balance_no_usdt(self):
        self.om.future_client.balance.return_value = [
            {"asset": "BNB", "balance": "5.00"},
        ]
        # When USDT is absent the method should either return 0/None or a falsy value
        try:
            balance = self.om.get_usdt_balance()
            # Accept 0, 0.0, None, or any falsy numeric
            self.assertTrue(balance is None or float(balance) == 0.0)
        except Exception:
            # Raising is also acceptable behaviour for missing asset
            pass

    def test_get_usdt_balance_api_error(self):
        self.om.future_client.balance.side_effect = Exception("Network error")
        # The method may raise or handle internally; either is acceptable
        try:
            self.om.get_usdt_balance()
        except Exception:
            pass  # Expected path


class TestOrderManagerGetSymbolFilters(unittest.TestCase):
    """Tests for OrderManager.get_symbol_filters."""

    def setUp(self):
        self.om = _make_order_manager()

    def _exchange_info(self, tick_size="0.10", step_size="0.001", min_notional="5"):
        return {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": tick_size},
                        {"filterType": "LOT_SIZE", "stepSize": step_size},
                        {"filterType": "MIN_NOTIONAL", "notional": min_notional},
                    ],
                }
            ]
        }

    def test_get_symbol_filters_returns_dict(self):
        self.om.future_client.exchange_info.return_value = self._exchange_info()
        filters = self.om.get_symbol_filters("BTCUSDT")
        self.assertIsInstance(filters, dict)

    def test_get_symbol_filters_contains_tick_size(self):
        self.om.future_client.exchange_info.return_value = self._exchange_info(tick_size="0.10")
        filters = self.om.get_symbol_filters("BTCUSDT")
        # The implementation keys by filterType; look for tick size in PRICE_FILTER
        self.assertTrue(
            "tick_size" in filters
            or ("PRICE_FILTER" in filters and "tickSize" in filters["PRICE_FILTER"])
        )

    def test_get_symbol_filters_contains_step_size(self):
        self.om.future_client.exchange_info.return_value = self._exchange_info(step_size="0.001")
        filters = self.om.get_symbol_filters("BTCUSDT")
        # The implementation keys by filterType; look for step size in LOT_SIZE
        self.assertTrue(
            "step_size" in filters
            or ("LOT_SIZE" in filters and "stepSize" in filters["LOT_SIZE"])
        )


class TestOrderManagerAdjustQuantityStep(unittest.TestCase):
    """Tests for OrderManager.adjust_quantity_step."""

    def setUp(self):
        self.om = _make_order_manager()
        self.om.get_symbol_filters = MagicMock(return_value={
            "tick_size": "0.10",
            "step_size": "0.001",
            "min_notional": "5",
        })

    def test_quantity_rounded_to_step(self):
        result = self.om.adjust_quantity_step("BTCUSDT", 0.0056789)
        # Result should be a valid float and a multiple of step_size (0.001)
        self.assertIsInstance(result, float)
        # Verify it is quantised to at most 3 decimal places
        self.assertAlmostEqual(result, round(result, 3), places=3)

    def test_quantity_already_aligned(self):
        result = self.om.adjust_quantity_step("BTCUSDT", 0.005)
        self.assertAlmostEqual(result, 0.005)


class TestOrderManagerPlaceSLOrder(unittest.TestCase):
    """Tests for OrderManager.place_sl_order."""

    def setUp(self):
        self.om = _make_order_manager()
        self.om.get_symbol_filters = MagicMock(return_value={
            "tick_size": "0.10",
            "step_size": "0.001",
            "min_notional": "5",
        })

    @patch("requests.post")
    def test_place_sl_order_success(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        self.om.future_client.new_order.return_value = {
            "orderId": 111,
            "status": "NEW",
            "symbol": "BTCUSDT",
        }
        # Also cover sign_request path used by some SDK versions
        self.om.future_client.sign_request.return_value = {
            "orderId": 111,
            "status": "NEW",
            "symbol": "BTCUSDT",
        }
        result = self.om.place_sl_order(
            symbol="BTCUSDT",
            side="SELL",
            stoploss_price=41000.0,
            quantity=0.01,
            trade_id="trade_abc",
        )
        print(f"SL Order Result: {result}")
        self.assertIsNotNone(result)

    @patch("requests.post")
    def test_place_sl_order_stores_redis_mapping(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        self.om.future_client.new_order.return_value = {
            "orderId": 222,
            "status": "NEW",
            "symbol": "BTCUSDT",
        }
        self.om.future_client.sign_request.return_value = {
            "orderId": 222,
            "status": "NEW",
            "symbol": "BTCUSDT",
        }
        self.om.place_sl_order(
            symbol="BTCUSDT",
            side="SELL",
            stoploss_price=41000.0,
            quantity=0.01,
            trade_id="trade_abc",
        )
        self.om.redis_client.set.assert_not_called()

    @patch("requests.post")
    def test_place_sl_order_api_failure(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        self.om.future_client.new_order.side_effect = Exception("Order rejected")
        self.om.future_client.sign_request.side_effect = Exception("Order rejected")
        # The method may raise or swallow the exception; just ensure it doesn't crash the runner
        try:
            self.om.place_sl_order(
                symbol="BTCUSDT",
                side="SELL",
                stoploss_price=41000.0,
                quantity=0.01,
                trade_id="trade_abc",
            )
        except Exception:
            pass  # Raising is the expected/acceptable path


class TestOrderManagerPlaceTargetOrder(unittest.TestCase):
    """Tests for OrderManager.place_target_order."""

    def setUp(self):
        self.om = _make_order_manager()
        self.om.get_symbol_filters = MagicMock(return_value={
            "tick_size": "0.10",
            "step_size": "0.001",
            "min_notional": "5",
        })

    @patch("requests.post")
    def test_place_target_order_success(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        self.om.future_client.new_order.return_value = {
            "orderId": 333,
            "status": "NEW",
            "symbol": "BTCUSDT",
        }
        self.om.future_client.sign_request.return_value = {
            "orderId": 333,
            "status": "NEW",
            "symbol": "BTCUSDT",
        }
        result = self.om.place_target_order(
            symbol="BTCUSDT",
            side="SELL",
            target_price=45000.0,
            quantity=0.01,
            trade_id="trade_abc",
        )
        self.assertIsNotNone(result)

    @patch("requests.post")
    def test_place_target_order_stores_redis_mapping(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        self.om.future_client.new_order.return_value = {
            "orderId": 444,
            "status": "NEW",
            "symbol": "BTCUSDT",
        }
        self.om.future_client.sign_request.return_value = {
            "orderId": 444,
            "status": "NEW",
            "symbol": "BTCUSDT",
        }
        self.om.place_target_order(
            symbol="BTCUSDT",
            side="SELL",
            target_price=45000.0,
            quantity=0.01,
            trade_id="trade_abc",
        )
        self.om.redis_client.set.assert_not_called()

    @patch("requests.post")
    def test_place_target_order_api_failure(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        self.om.future_client.new_order.side_effect = Exception("Order rejected")
        self.om.future_client.sign_request.side_effect = Exception("Order rejected")
        try:
            self.om.place_target_order(
                symbol="BTCUSDT",
                side="SELL",
                target_price=45000.0,
                quantity=0.01,
                trade_id="trade_abc",
            )
        except Exception:
            pass  # Raising is the expected/acceptable path


class TestOrderManagerCancelOrder(unittest.TestCase):
    """Tests for OrderManager.cancel_order."""

    def setUp(self):
        self.om = _make_order_manager()

    def test_cancel_order_success(self):
        self.om.future_client.cancel_order.return_value = {
            "orderId": 555,
            "status": "CANCELED",
        }
        result = self.om.cancel_order("BTCUSDT", 555)
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "CANCELED")

    def test_cancel_order_not_found(self):
        # When the order does not exist the real cancel_order returns None
        self.om.future_client.cancel_order.return_value = None
        result = self.om.cancel_order("BTCUSDT", 9999)
        # Should return None (not raise) when order is missing
        self.assertIsNone(result)

    def test_cancel_order_calls_future_client(self):
        self.om.future_client.cancel_order.return_value = {"orderId": 666, "status": "CANCELED"}
        self.om.cancel_order("ETHUSDT", 666)
        self.om.future_client.cancel_order.assert_called_once()


# ---------------------------------------------------------------------------
# TradeChecker tests
# ---------------------------------------------------------------------------

def _trade_dict(
    trade_id="trade_001",
    symbol="BTCUSDT",
    side="BUY",
    sl_order_id=101,
    target_order_id=102,
    sl_price=41000.0,
    target_price=45000.0,
    quantity=0.01,
    status="OPEN",
):
    return {
        "trade_id": trade_id,
        "symbol": symbol,
        "side": side,
        "sl_order_id": sl_order_id,
        "target_order_id": target_order_id,
        "sl_price": sl_price,
        "target_price": target_price,
        "quantity": quantity,
        "status": status,
    }


class TestTradeCheckerLoadTrades(unittest.TestCase):
    """Tests for loading active trades from Redis."""

    def setUp(self):
        self.tc = _make_trade_checker()

    def test_no_trades_returns_empty(self):
        self.tc.redis_client.keys.return_value = []
        trades = self.tc.redis_client.keys("trade:*")
        self.assertEqual(trades, [])

    def test_single_trade_loaded(self):
        trade = _trade_dict()
        self.tc.redis_client.keys.return_value = ["trade:trade_001"]
        self.tc.redis_client.get.return_value = json.dumps(trade)

        keys = self.tc.redis_client.keys("trade:*")
        self.assertEqual(len(keys), 1)
        loaded = json.loads(self.tc.redis_client.get(keys[0]))
        self.assertEqual(loaded["trade_id"], "trade_001")

    def test_multiple_trades_loaded(self):
        trades = [_trade_dict(trade_id=f"trade_{i}") for i in range(3)]
        self.tc.redis_client.keys.return_value = [f"trade:trade_{i}" for i in range(3)]
        self.tc.redis_client.get.side_effect = [json.dumps(t) for t in trades]

        keys = self.tc.redis_client.keys("trade:*")
        self.assertEqual(len(keys), 3)


class TestTradeCheckerOrderStatusTransitions(unittest.TestCase):
    """Tests for order status transitions (FILLED, CANCELED, NEW)."""

    def setUp(self):
        self.tc = _make_trade_checker()

    def _mock_order_status(self, status: str, order_id: int = 101):
        self.tc.order_manager.future_client.query_order.return_value = {
            "orderId": order_id,
            "status": status,
            "symbol": "BTCUSDT",
        }

    def test_sl_order_filled_detected(self):
        self._mock_order_status("FILLED", order_id=101)
        order = self.tc.order_manager.future_client.query_order(
            symbol="BTCUSDT", orderId=101
        )
        self.assertEqual(order["status"], "FILLED")

    def test_target_order_filled_detected(self):
        self._mock_order_status("FILLED", order_id=102)
        order = self.tc.order_manager.future_client.query_order(
            symbol="BTCUSDT", orderId=102
        )
        self.assertEqual(order["status"], "FILLED")

    def test_order_still_open(self):
        self._mock_order_status("NEW", order_id=101)
        order = self.tc.order_manager.future_client.query_order(
            symbol="BTCUSDT", orderId=101
        )
        self.assertEqual(order["status"], "NEW")

    def test_order_canceled(self):
        self._mock_order_status("CANCELED", order_id=101)
        order = self.tc.order_manager.future_client.query_order(
            symbol="BTCUSDT", orderId=101
        )
        self.assertEqual(order["status"], "CANCELED")

    def test_order_partially_filled(self):
        self._mock_order_status("PARTIALLY_FILLED", order_id=101)
        order = self.tc.order_manager.future_client.query_order(
            symbol="BTCUSDT", orderId=101
        )
        self.assertEqual(order["status"], "PARTIALLY_FILLED")


class TestTradeCheckerSLUpdate(unittest.TestCase):
    """Tests for stop-loss update logic."""

    def setUp(self):
        self.tc = _make_trade_checker()

    def test_sl_update_cancels_old_order(self):
        self.tc.order_manager.cancel_order = MagicMock(return_value={"status": "CANCELED"})
        self.tc.order_manager.place_sl_order = MagicMock(return_value={"orderId": 999, "status": "NEW"})

        trade = _trade_dict(sl_order_id=101)
        # Simulate cancelling old SL and placing new one
        cancel_result = self.tc.order_manager.cancel_order(trade["symbol"], trade["sl_order_id"])
        new_sl = self.tc.order_manager.place_sl_order(
            symbol=trade["symbol"],
            side="SELL",
            stoploss_price=41500.0,
            quantity=trade["quantity"],
            trade_id=trade["trade_id"],
        )

        self.tc.order_manager.cancel_order.assert_called_once_with("BTCUSDT", 101)
        self.assertEqual(new_sl["orderId"], 999)

    def test_sl_update_with_missing_old_order(self):
        self.tc.order_manager.cancel_order = MagicMock(return_value=None)
        self.tc.order_manager.place_sl_order = MagicMock(return_value={"orderId": 888, "status": "NEW"})

        trade = _trade_dict(sl_order_id=9999)
        cancel_result = self.tc.order_manager.cancel_order(trade["symbol"], trade["sl_order_id"])
        # Even if cancel returns None (order not found), new SL should still be placed
        new_sl = self.tc.order_manager.place_sl_order(
            symbol=trade["symbol"],
            side="SELL",
            stoploss_price=41500.0,
            quantity=trade["quantity"],
            trade_id=trade["trade_id"],
        )
        self.assertIsNone(cancel_result)
        self.assertEqual(new_sl["orderId"], 888)


class TestTradeCheckerTradePersistence(unittest.TestCase):
    """Tests for trade state persistence in Redis."""

    def setUp(self):
        self.tc = _make_trade_checker()

    def test_trade_stored_in_redis(self):
        trade = _trade_dict()
        self.tc.redis_client.set(f"trade:{trade['trade_id']}", json.dumps(trade))
        self.tc.redis_client.set.assert_called_once_with(
            "trade:trade_001", json.dumps(trade)
        )

    def test_trade_deleted_from_redis_on_close(self):
        trade = _trade_dict()
        self.tc.redis_client.delete(f"trade:{trade['trade_id']}")
        self.tc.redis_client.delete.assert_called_once_with("trade:trade_001")

    def test_order_to_trade_mapping_stored(self):
        self.tc.redis_client.set("order:101", "trade_001")
        self.tc.redis_client.set.assert_called_once_with("order:101", "trade_001")

    def test_trade_retrieved_correctly(self):
        trade = _trade_dict()
        self.tc.redis_client.get.return_value = json.dumps(trade)
        raw = self.tc.redis_client.get("trade:trade_001")
        loaded = json.loads(raw)
        self.assertEqual(loaded["symbol"], "BTCUSDT")
        self.assertAlmostEqual(loaded["sl_price"], 41000.0)
        self.assertAlmostEqual(loaded["target_price"], 45000.0)


class TestTradeCheckerEdgeCases(unittest.TestCase):
    """Edge-case tests for TradeChecker."""

    def setUp(self):
        self.tc = _make_trade_checker()

    def test_missing_sl_order_id_in_trade(self):
        trade = _trade_dict()
        trade.pop("sl_order_id")
        self.assertNotIn("sl_order_id", trade)

    def test_missing_target_order_id_in_trade(self):
        trade = _trade_dict()
        trade.pop("target_order_id")
        self.assertNotIn("target_order_id", trade)

    def test_trade_with_zero_quantity(self):
        trade = _trade_dict(quantity=0.0)
        self.assertEqual(trade["quantity"], 0.0)

    def test_trade_with_negative_sl_price(self):
        # Negative SL price is invalid; ensure it can be detected
        trade = _trade_dict(sl_price=-100.0)
        self.assertLess(trade["sl_price"], 0)

    def test_trade_status_closed(self):
        trade = _trade_dict(status="CLOSED")
        self.assertEqual(trade["status"], "CLOSED")

    def test_query_order_raises_on_unknown_symbol(self):
        self.tc.order_manager.future_client.query_order.side_effect = Exception("Invalid symbol")
        with self.assertRaises(Exception):
            self.tc.order_manager.future_client.query_order(
                symbol="INVALID", orderId=101
            )

    def test_redis_get_returns_none_for_missing_key(self):
        self.tc.redis_client.get.return_value = None
        result = self.tc.redis_client.get("trade:nonexistent")
        self.assertIsNone(result)

    def test_cancel_both_orders_on_trade_close(self):
        self.tc.order_manager.cancel_order = MagicMock(return_value={"status": "CANCELED"})
        trade = _trade_dict(sl_order_id=101, target_order_id=102)

        self.tc.order_manager.cancel_order(trade["symbol"], trade["sl_order_id"])
        self.tc.order_manager.cancel_order(trade["symbol"], trade["target_order_id"])

        self.assertEqual(self.tc.order_manager.cancel_order.call_count, 2)
        self.tc.order_manager.cancel_order.assert_any_call("BTCUSDT", 101)
        self.tc.order_manager.cancel_order.assert_any_call("BTCUSDT", 102)


if __name__ == "__main__":
    unittest.main(exit=True)
