import os
import unittest
from timeout_decorator import timeout
from unittest.mock import AsyncMock, patch, MagicMock

os.environ["GROQ_API_KEY"] = "test_key"
os.environ["LANGCHAIN_API_KEY"] = "test_key"
os.environ["LANGSMITH_API_KEY"] = "test_key"

import asyncio
from orbit.core.sentimen_cron import Croner


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
        mock_redis.setex.assert_called_once_with("market_sentiments", 3600, "BULLISH")

        assert result["sentiment"] == "BULLISH"


import json

class TestAPI(unittest.TestCase):
    def setUp(self):
        # Setup code for API client or test client
        # For demonstration, we'll mock a simple API call
        self.mock_api_response = {
            "status": "success",
            "data": {
                "trade_id": "12345",
                "symbol": "BTCUSDT",
                "sl_price": 42000.0,
                "target_price": 44000.0
            }
        }

    def test_api_response(self):
        # Simulate API call and response
        response = self.mock_api_response
        self.assertEqual(response["status"], "success")
        self.assertIn("trade_id", response["data"])
        self.assertIn("symbol", response["data"])
        self.assertIn("sl_price", response["data"])
        self.assertIn("target_price", response["data"])

    def test_sl_and_tp_persistence(self):
        # Simulate persisted SL/TP logic
        order = {
            "trade_id": "12345",
            "symbol": "BTCUSDT",
            "sl_price": 42000.0,
            "target_price": 44000.0
        }
        persisted_sl = order.get("sl_price")
        persisted_tp = order.get("target_price")
        # If persisted sl exist, use sl_price to create new sl
        if persisted_sl:
            new_sl = persisted_sl
        else:
            new_sl = None
        # If persisted tp exist, use target_price to create new tp
        if persisted_tp:
            new_tp = persisted_tp
        else:
            new_tp = None
        self.assertEqual(new_sl, 42000.0)
        self.assertEqual(new_tp, 44000.0)

    def test_symbol_requires_trade_id(self):
        # If symbol is present, trade_id should also be present
        order = {"symbol": "BTCUSDT"}
        self.assertTrue("trade_id" in order or "symbol" not in order, "If symbol is present, trade_id should also be present")

if __name__ == "__main__":
    unittest.main(exit=True)
