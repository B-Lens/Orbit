import unittest
from timeout_decorator import timeout
from unittest.mock import patch, MagicMock

from tqdm import asyncio
from orbit.core.sentimen_cron import Croner


class TestCore(unittest.TestCase):

    @timeout(30)
    @patch("orbit.core.sentimen_cron.SentimentWorkflow")
    def test_sentiment(self, MockWorkflow):
        mock_instance = MagicMock()
        mock_instance.run_analysis.return_value = {"success": True}
        MockWorkflow.return_value = mock_instance

        croner = Croner()
        return asyncio.run(croner.run_once())

if __name__ == "__main__":
    unittest.main(exit=True)
