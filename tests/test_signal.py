import unittest
from unittest.mock import MagicMock

import pandas as pd

from orbit.core.mongo_handler import MongoHandler


class TestDataCollector(unittest.TestCase):
    def test_decision_event_identity_prevents_duplicate_append(self):
        handler = MongoHandler.__new__(MongoHandler)
        handler.decision_collection = MagicMock()

        handler.append_decision_event(
            "decision-1",
            {"event_id": "order_submitted:BTCUSDT:123", "status": "order_submitted"},
        )

        query = handler.decision_collection.update_one.call_args.args[0]
        self.assertEqual(query["decision_id"], "decision-1")
        self.assertEqual(
            query["execution_events.event_id"],
            {"$ne": "order_submitted:BTCUSDT:123"},
        )

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
