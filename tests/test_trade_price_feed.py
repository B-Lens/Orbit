import time
from unittest.mock import MagicMock

from orbit.core.trade_checker import TradeChecker


def test_stale_websocket_price_is_not_used_when_rest_fallback_fails() -> None:
    checker = TradeChecker.__new__(TradeChecker)
    checker.live_prices = {"PAXGUSDT": (4400.0, time.time() - 10)}
    checker.get_future_symbol_price = MagicMock(side_effect=ValueError("bad ticker"))

    assert checker.check_price_freshness("PAXGUSDT") is None
    assert checker.live_prices["PAXGUSDT"][0] == 4400.0


def test_invalid_websocket_price_is_replaced_with_valid_rest_price() -> None:
    checker = TradeChecker.__new__(TradeChecker)
    checker.live_prices = {"SKYUSDT": (0.0, time.time())}
    checker.get_future_symbol_price = MagicMock(return_value=0.05)

    assert checker.check_price_freshness("SKYUSDT") == 0.05
    assert checker.live_prices["SKYUSDT"][0] == 0.05
