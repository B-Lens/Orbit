import unittest
from unittest.mock import MagicMock

import pandas as pd

from orbit.core.mongo_handler import MongoHandler


class TestDataCollector(unittest.TestCase):
    def test_converts_binance_klines_without_network_or_database(self):
        handler = MongoHandler.__new__(MongoHandler)
        handler.get_binance_klines = MagicMock(
            side_effect=[
                [
                    [
                        1_700_000_000_000,
                        "100",
                        "105",
                        "99",
                        "103",
                        "42",
                        1_700_000_899_999,
                        "0",
                        1,
                        "0",
                        "0",
                        "0",
                    ]
                ],
                [],
            ]
        )

        frame = handler.data_collector("BTCUSDT", start_time=1_700_000_000_000)

        self.assertIsInstance(frame, pd.DataFrame)
        self.assertEqual(
            frame.iloc[0][["open", "high", "low", "close", "volume"]].tolist(),
            [100, 105, 99, 103, 42],
        )
        self.assertEqual(frame.index.name, "timestamp")


if __name__ == "__main__":
    unittest.main()
