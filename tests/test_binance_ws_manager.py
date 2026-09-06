import json
from unittest.mock import MagicMock

from orbit.core.binance_ws_manager import BinanceWSManager


def test_stream_url_uses_periodic_symbol_tickers() -> None:
    manager = BinanceWSManager(["BTCUSDT", "PAXGUSDT"], MagicMock())

    assert manager._stream_url() == (
        "wss://fstream.binance.com/stream?streams="
        "btcusdt@ticker/paxgusdt@ticker"
    )


def test_ticker_message_publishes_latest_price() -> None:
    on_price_update = MagicMock()
    manager = BinanceWSManager(["PAXGUSDT"], on_price_update)

    manager._on_message(
        MagicMock(),
        json.dumps({"data": {"s": "PAXGUSDT", "c": "4382.40"}}),
    )

    symbol, price, timestamp = on_price_update.call_args.args
    assert symbol == "PAXGUSDT"
    assert price == 4382.40
    assert timestamp == manager._last_message_time
