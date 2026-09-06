import time
from unittest.mock import MagicMock, patch

import pytest

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


def test_price_outage_does_not_skip_protective_order_reconciliation() -> None:
    checker = TradeChecker.__new__(TradeChecker)
    trade = {
        "trade_id": "trade-1",
        "stop_loss_price": 4300.0,
        "target": 4500.0,
        "stop_loss_order": {"algoId": "101"},
    }
    checker.trades = {"PAXGUSDT": trade}
    checker._ws_manager = MagicMock()
    checker._ensure_ws = MagicMock()
    checker.check_price_freshness = MagicMock(return_value=None)
    checker.activePosition_coolMaker = MagicMock(
        return_value={"PAXGUSDT": trade}
    )
    checker.ensure_orders = MagicMock(side_effect=KeyboardInterrupt)
    indian_time = MagicMock()
    indian_time.minute = 0

    with patch("orbit.core.trade_checker.get_indian_time", return_value=indian_time):
        with pytest.raises(KeyboardInterrupt):
            checker.monitor_trades(["PAXGUSDT"], {})

    checker.ensure_orders.assert_called_once_with("PAXGUSDT", trade, {})
