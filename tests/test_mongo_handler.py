from unittest.mock import MagicMock, patch

import pandas as pd
import requests

from orbit.core.mongo_handler import (
    BINANCE_FUTURES_TESTNET_KLINES_URL,
    MongoHandler,
)


def _handler_without_database() -> MongoHandler:
    return MongoHandler.__new__(MongoHandler)


@patch("orbit.core.mongo_handler.requests.get")
def test_xau_klines_use_futures_testnet(mock_get):
    response = MagicMock()
    response.json.return_value = [[1, "2"]]
    mock_get.return_value = response

    result = _handler_without_database().get_binance_klines(
        "XAUUSDT", "15m", 1, 2
    )

    assert result == [[1, "2"]]
    mock_get.assert_called_once_with(
        BINANCE_FUTURES_TESTNET_KLINES_URL,
        params={
            "symbol": "XAUUSDT",
            "interval": "15m",
            "limit": 1000,
            "startTime": 1,
            "endTime": 2,
        },
        timeout=10,
    )


@patch("orbit.core.mongo_handler.time.sleep")
@patch("orbit.core.mongo_handler.requests.get")
def test_non_retryable_binance_error_returns_immediately(mock_get, mock_sleep):
    response = MagicMock(status_code=400)
    mock_get.side_effect = requests.HTTPError(response=response)

    result = _handler_without_database().get_binance_klines("BAD", "15m", 1, 2)

    assert result == []
    mock_get.assert_called_once()
    mock_sleep.assert_not_called()


@patch("orbit.core.mongo_handler.time.sleep")
@patch("orbit.core.mongo_handler.requests.get")
def test_transient_binance_error_is_retried(mock_get, mock_sleep):
    unavailable = MagicMock(status_code=503)
    success = MagicMock()
    success.json.return_value = [[1, "2"]]
    mock_get.side_effect = [requests.HTTPError(response=unavailable), success]

    result = _handler_without_database().get_binance_klines("BTCUSDT", "15m", 1, 2)

    assert result == [[1, "2"]]
    assert mock_get.call_count == 2
    mock_sleep.assert_called_once_with(1)


def test_xau_history_is_isolated_under_testnet_cache_key():
    handler = _handler_without_database()
    handler.get_mongo_historical_data = MagicMock(return_value=pd.DataFrame())
    collected = pd.DataFrame(
        {
            "open": [1.0, 2.0],
            "high": [2.0, 3.0],
            "low": [0.5, 1.5],
            "close": [1.5, 2.5],
            "volume": [10.0, 20.0],
        },
        index=pd.to_datetime(["2026-08-19 14:00", "2026-08-19 14:15"]),
    )
    collected.index.name = "timestamp"
    handler.data_collector = MagicMock(return_value=collected)
    handler.store_historical_data = MagicMock()

    result = handler.handle_mongo_data("XAUUSDT")

    assert len(result) == 1
    handler.get_mongo_historical_data.assert_called_once_with(
        "XAUUSDT_TESTNET", interval="15m"
    )
    handler.data_collector.assert_called_once_with(
        "XAUUSDT", interval="15m", start_time=None
    )
    stored_symbol, stored_data = handler.store_historical_data.call_args.args
    assert stored_symbol == "XAUUSDT_TESTNET"
    assert len(stored_data) == 1
