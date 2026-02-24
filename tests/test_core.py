import unittest
from timeout_decorator import timeout
from unittest.mock import AsyncMock, patch, MagicMock

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


if __name__ == "__main__":
    unittest.main(exit=True)
