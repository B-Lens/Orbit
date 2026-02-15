import unittest
from timeout_decorator import timeout


from orbit.core.sentimen_cron import Croner

croner = Croner(isTesting=True)


class TestMain(unittest.TestCase):

    @timeout(15)
    def test_sentiment_croner(self):
        self.assertTrue(croner.sentiment_croner())

if __name__ == "__main__":
    unittest.main(exit=True)
