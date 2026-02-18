import os
import unittest
from unittest.mock import patch, MagicMock
from timeout_decorator import timeout

# ---------------------------------------------------
# Set fake env variables BEFORE importing inference
# ---------------------------------------------------
os.environ["OPENAI_API_KEY"] = "test"
os.environ["GROQ_API_KEY"] = "test"
os.environ["LANGSMITH_API_KEY"] = "test"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from orbit.market_intelligence.lang_inference_workflow import inference


class TestAI(unittest.TestCase):

    @timeout(20)
    @patch("orbit.ai.clients.news_client.fetch_news_articles")
    @patch("orbit.ai.lang_inference_workflow.fetch_reddit_posts")
    @patch("orbit.ai.utils.utils.fetch_market_indicators")
    @patch("orbit.ai.utils.utils.initialize_llm")
    def test_langchain_inference(
        self,
        mock_llm,
        mock_market,
        mock_reddit,
        mock_news
    ):
        # -----------------------------
        # Mock LLM response
        # -----------------------------
        fake_response = (
            "Sentiment: bullish, Confidence: 0.8, "
            "Explanation: Test sentiment response"
        )

        mock_llm.invoke.return_value = fake_response

        # -----------------------------
        # Mock external data sources
        # -----------------------------
        mock_news.return_value = "Positive economic news"
        mock_reddit.return_value = ["Market looks strong"]
        mock_market.return_value = None

        # -----------------------------
        # Run inference
        # -----------------------------
        result = inference()

        # -----------------------------
        # Assertions
        # -----------------------------
        self.assertIsInstance(result, dict)

        required_keys = ["sentiment", "confidence", "explanation"]
        for key in required_keys:
            self.assertIn(key, result)

        self.assertIsInstance(result["sentiment"], str)
        self.assertIsInstance(result["confidence"], (int, float))
        self.assertIsInstance(result["explanation"], str)


if __name__ == "__main__":
    unittest.main(exit=True)
