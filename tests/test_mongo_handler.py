from unittest.mock import MagicMock, patch

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


def test_decision_event_identity_prevents_duplicate_append() -> None:
    handler = MongoHandler.__new__(MongoHandler)
    handler.decision_collection = MagicMock()
    handler.decision_collection.find_one.return_value = {"_id": "stored"}

    stored = handler.append_decision_event(
        "decision-1",
        {"event_id": "order_submitted:BTCUSDT:123", "status": "order_submitted"},
    )

    assert stored is True
    query = handler.decision_collection.update_one.call_args.args[0]
    assert query["decision_id"] == "decision-1"
    assert query["execution_events.event_id"] == {
        "$ne": "order_submitted:BTCUSDT:123"
    }


def test_decision_event_reports_failed_durability_check() -> None:
    handler = MongoHandler.__new__(MongoHandler)
    handler.decision_collection = MagicMock()
    handler.decision_collection.find_one.return_value = None

    stored = handler.append_decision_event(
        "decision-1",
        {"event_id": "trade_closed:decision-1", "status": "trade_closed"},
    )

    assert stored is False


def test_read_only_handler_does_not_create_or_modify_indexes() -> None:
    mongo_client = MagicMock()

    handler = MongoHandler(mongo_client=mongo_client, read_only=True)

    assert handler.decision_collection is mongo_client["orbit"]["trade_decisions"]
    assert handler.testnet_collection is mongo_client["orbit"]["OHLCVDataTestnet"]
    handler.collection.create_index.assert_not_called()
    handler.decision_collection.create_index.assert_not_called()
    handler.income_collection.drop_index.assert_not_called()


@patch("orbit.core.mongo_handler.requests.get")
def test_testnet_klines_use_futures_testnet_endpoint(mock_get: MagicMock) -> None:
    handler = MongoHandler.__new__(MongoHandler)
    response = mock_get.return_value
    response.json.return_value = []

    handler.get_binance_klines(
        "BTCUSDT", "15m", 1_700_000_000_000, 1_700_000_900_000, "testnet"
    )

    assert mock_get.call_args.args[0] == (
        "https://demo-fapi.binance.com/fapi/v1/klines"
    )


def test_testnet_ohlcv_is_stored_in_separate_collection() -> None:
    handler = MongoHandler.__new__(MongoHandler)
    handler.collection = MagicMock()
    handler.testnet_collection = MagicMock()
    frame = _ohlcv_frame(pd.Timestamp("2023-11-14 22:13:20")).reset_index()
    frame["timestamp"] = frame["timestamp"].astype("int64") // 1_000_000_000

    handler.store_historical_data("BTCUSDT", frame, execution_mode="testnet")

    handler.testnet_collection.insert_many.assert_called_once()
    handler.collection.insert_many.assert_not_called()


def test_data_collector_converts_binance_klines() -> None:
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

    assert isinstance(frame, pd.DataFrame)
    assert frame.iloc[0][["open", "high", "low", "close", "volume"]].tolist() == [
        100,
        105,
        99,
        103,
        42,
    ]
    assert frame.index.name == "timestamp"


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
        "BTCUSDT",
        interval="15m",
        start_time=1_700_000_900_000,
        execution_mode="live",
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
