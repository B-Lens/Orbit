from unittest.mock import MagicMock

from orbit.core.execution import ExecutionMode, ExecutionSettings
from orbit.core.order_manager import OrderManager
from scripts import check_testnet_orders as SCRIPT


def _manager() -> MagicMock:
    manager = MagicMock(spec=OrderManager)
    manager.execution_settings = ExecutionSettings(
        {
            "BTCUSDT": ExecutionMode.TESTNET,
            "ETHUSDT": ExecutionMode.TESTNET,
            "PAXGUSDT": ExecutionMode.LIVE,
        }
    )
    manager.config = {
        "risk_management": {"BTCUSDT": 0.01, "ETHUSDT": 0.01},
        "FUTURE_LEVERAGE": 2,
    }
    return manager


def test_successful_probe_is_cancelled():
    manager = _manager()
    manager.get_open_orders.return_value = []
    manager.get_conditional_open_orders.return_value = []
    manager.get_symbol_price.return_value = 100.0
    manager.place_order.return_value = ({"orderId": 123}, 0.1, {})
    manager.cancel_order.return_value = {"orderId": 123, "status": "CANCELED"}

    result = SCRIPT.check_symbol(manager, "BTCUSDT", 10.0)

    assert result.status == "PASSED"
    manager.place_order.assert_called_once_with(
        risk_management=manager.config["risk_management"],
        symbol="BTCUSDT",
        side="BUY",
        price=90.0,
        sl=89.1,
        target=91.8,
        leverage=2,
        ros=True,
    )
    manager.cancel_order.assert_called_once_with("BTCUSDT", 123)


def test_existing_open_order_skips_submission():
    manager = _manager()
    manager.get_open_orders.return_value = [{"orderId": 7, "status": "NEW"}]
    manager.get_conditional_open_orders.return_value = []

    result = SCRIPT.check_symbol(manager, "BTCUSDT", 10.0)

    assert result.status == "SKIPPED"
    manager.place_order.assert_not_called()


def test_exchange_rejection_is_reported_and_other_assets_continue():
    manager = _manager()
    manager.get_open_orders.return_value = []
    manager.get_conditional_open_orders.return_value = []
    manager.get_symbol_price.return_value = 100.0
    manager.place_order.side_effect = [
        (None, None, None),
        ({"orderId": 456}, 0.1, {}),
    ]
    manager.cancel_order.return_value = {"orderId": 456, "status": "CANCELED"}

    results = SCRIPT.run_checks(manager, 10.0)

    assert [result.status for result in results] == ["FAILED", "PASSED"]
    assert "place_order rejected" in results[0].detail


def test_run_checks_excludes_live_assets():
    manager = _manager()
    manager.get_open_orders.return_value = [{"orderId": 7, "status": "NEW"}]
    manager.get_conditional_open_orders.return_value = []

    results = SCRIPT.run_checks(manager, 10.0)

    assert [result.symbol for result in results] == ["BTCUSDT", "ETHUSDT"]
    checked_symbols = [call.args[0] for call in manager.get_open_orders.call_args_list]
    assert checked_symbols == ["BTCUSDT", "ETHUSDT"]


def test_invalid_discount_is_rejected_before_checking_assets():
    manager = _manager()

    try:
        SCRIPT.run_checks(manager, 0)
    except ValueError as error:
        assert "discount" in str(error)
    else:
        raise AssertionError("invalid discount was not rejected")

    manager.get_open_orders.assert_not_called()
