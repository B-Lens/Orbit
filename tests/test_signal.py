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

    def test_handle_mongo_data_normalizes_legacy_microsecond_timestamps(self):
        handler = MongoHandler.__new__(MongoHandler)
        handler.get_mongo_historical_data = MagicMock(
            return_value=pd.DataFrame(
                [
                    {
                        "timestamp": 1_700_000_000_000_000,
                        "open": 100.0,
                        "high": 105.0,
                        "low": 99.0,
                        "close": 103.0,
                        "volume": 42.0,
                    }
                ]
            )
        )
        new_index = pd.to_datetime([1_700_000_900, 1_700_001_800], unit="s")
        handler.data_collector = MagicMock(
            return_value=pd.DataFrame(
                {
                    "open": [103.0, 104.0],
                    "high": [106.0, 107.0],
                    "low": [102.0, 103.0],
                    "close": [105.0, 106.0],
                    "volume": [40.0, 41.0],
                },
                index=pd.Index(new_index, name="timestamp"),
            )
        )
        handler.store_historical_data = MagicMock()

        result = handler.handle_mongo_data("SKYUSDT")

        self.assertEqual(result.index[0], pd.Timestamp("2023-11-14 22:13:20"))
        self.assertEqual(
            handler.data_collector.call_args.kwargs["start_time"], 1_700_000_900_000
        )
        stored = handler.store_historical_data.call_args.args[1]
        self.assertEqual(stored["timestamp"].tolist(), [1_700_000_900])


if __name__ == "__main__":
    unittest.main()
