from unittest.mock import MagicMock

import pandas as pd

from orbit.core.mongo_handler import MongoHandler


def _ohlcv_frame(timestamp: pd.Timestamp) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [100.0],
            "high": [105.0],
            "low": [99.0],
            "close": [103.0],
            "volume": [42.0],
        },
        index=pd.DatetimeIndex([timestamp], name="timestamp"),
    )


def test_handle_mongo_data_normalizes_legacy_microsecond_timestamp() -> None:
    handler = MongoHandler.__new__(MongoHandler)
    handler.get_mongo_historical_data = MagicMock(
        return_value=pd.DataFrame(
            {
                "timestamp": [1_700_000_000_000_000],
                "open": [100.0],
                "high": [105.0],
                "low": [99.0],
                "close": [103.0],
                "volume": [42.0],
            }
        )
    )
    handler.data_collector = MagicMock(return_value=pd.DataFrame())
    handler.store_historical_data = MagicMock()

    result = handler.handle_mongo_data("BTCUSDT")

    assert result.index[0] == pd.Timestamp("2023-11-14 22:13:20")
    handler.data_collector.assert_called_once_with(
        "BTCUSDT", interval="15m", start_time=1_700_000_900_000
    )


def test_handle_mongo_data_persists_datetime_index_as_epoch_seconds() -> None:
    timestamp = pd.Timestamp("2023-11-14 22:13:20")
    handler = MongoHandler.__new__(MongoHandler)
    handler.get_mongo_historical_data = MagicMock(return_value=pd.DataFrame())
    handler.data_collector = MagicMock(
        return_value=pd.concat(
            [
                _ohlcv_frame(timestamp),
                _ohlcv_frame(timestamp + pd.Timedelta(minutes=15)),
            ]
        )
    )
    handler.store_historical_data = MagicMock()

    handler.handle_mongo_data("BTCUSDT")

    stored = handler.store_historical_data.call_args.args[1]
    assert stored["timestamp"].tolist() == [1_700_000_000]


def test_store_historical_data_normalizes_timestamp_before_insert() -> None:
    handler = MongoHandler.__new__(MongoHandler)
    handler.collection = MagicMock()
    frame = _ohlcv_frame(pd.Timestamp("2023-11-14 22:13:20")).reset_index()
    frame["timestamp"] = frame["timestamp"].astype("int64") // 1_000

    handler.store_historical_data("BTCUSDT", frame)

    record = handler.collection.insert_many.call_args.args[0][0]
    assert record["timestamp"] == 1_700_000_000
