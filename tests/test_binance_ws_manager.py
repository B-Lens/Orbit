from orbit.core.binance_ws_manager import BinanceWSManager


def test_paxg_uses_regular_mark_price_updates() -> None:
    manager = BinanceWSManager(
        trading_pairs=["BTCUSDT", "PAXGUSDT"],
        on_price_update=lambda *_args: None,
    )

    assert manager._stream_url() == (
        "wss://fstream.binance.com/stream?streams="
        "btcusdt@trade/paxgusdt@markPrice@1s"
    )
