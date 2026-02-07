import unittest
from timeout_decorator import timeout

from orbit.core.sentimen_cron import Croner
from orbit.ai.lang_inference import inference
croner = Croner(isTesting=True)

class TestMain(unittest.TestCase):
 
    @timeout(15)
    def test_sentiment_croner(self):
        self.assertTrue(croner.sentiment_croner())

    @timeout(20)
    def test_langchain_inference(self):
        result = inference()
        # Check type
        self.assertIsInstance(result, dict, "Result should be a dictionary")

        # Check required keys exist
        required_keys = ["sentiment", "confidence", "explanation"]
        for key in required_keys:
            self.assertIn(key, result, f"Missing key: {key}")

        # Optional: Check types of values
        self.assertIsInstance(result["sentiment"], str)
        self.assertIsInstance(result["confidence"], (int, float))
        self.assertIsInstance(result["explanation"], str)

if __name__ == "__main__":
    unittest.main(exit=True)

