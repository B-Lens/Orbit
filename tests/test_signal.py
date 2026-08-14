import unittest
import pandas as pd
from datetime import datetime, timedelta
from timeout_decorator import timeout
from orbit.core.mongo_handler import MongoHandler


class TestMongo(unittest.TestCase):

    def setUp(self):
        self.mongo_handler = MongoHandler()

    @timeout(15)
    def test_data_collector(self):
        current_time = datetime.now()
        few_back = current_time - timedelta(minutes=45)
        timestamp = int(few_back.timestamp() * 1000)
        self.assertEqual(type(self.mongo_handler.data_collector(symbol="BTCUSDT", interval='15m', start_time=timestamp)), pd.DataFrame)

    def tearDown(self):
        self.mongo_handler.close()

if __name__ == "__main__":
    unittest.main()
