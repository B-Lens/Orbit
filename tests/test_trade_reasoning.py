from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from orbit.core.execution import ExecutionMode, ExecutionSettings
from orbit.core.main import BinanceAutomation
from orbit.core.mongo_handler import MongoHandler
from orbit.core.trade_checker import TradeChecker
from orbit.core.trade_reasoner import EntryReasoning, ExitReasoning, TradeReasoner


def _signal() -> dict:
    return {
        "decision_id": "decision-1",
        "symbol": "BTCUSDT",
        "signal": "BUY",
        "entry_price": 100.0,
        "stop_loss": 98.0,
        "take_profit": 104.0,
        "sentiment": "BULLISH",
    }


def test_trade_reasoner_parses_entry_decision() -> None:
    llm = MagicMock()
    llm.invoke.return_value = (
        '{"take_trade": true, "reasoning": "aligned", "confidence": 0.91}'
    )

    result = TradeReasoner(llm).review_entry(_signal())

    assert result == EntryReasoning(True, "aligned", 0.91)
    assert "market-intelligence sentiment" in llm.invoke.call_args.args[0]


def test_core_blocks_llm_rejected_trade_and_persists_reasoning() -> None:
    order_manager = MagicMock()
    trade_reasoner = MagicMock()
    trade_reasoner.review_entry.return_value = EntryReasoning(False, "weak setup", 0.8)
    automation = BinanceAutomation.__new__(BinanceAutomation)
    automation.order_manager = order_manager
    automation._trade_reasoner = trade_reasoner
    automation.future_leverage = 2
    automation.risk_management = {}
    automation.send_logs = MagicMock()
    automation.send_alerts = MagicMock()

    automation.process_signal(_signal())

    order_manager.place_order.assert_not_called()
    order_manager.mongo_handler.append_decision_event.assert_called_once()
    event = order_manager.mongo_handler.append_decision_event.call_args.args[1]
    assert event["status"] == "llm_entry_rejected"
    assert event["llm_reasoning"]["reasoning"] == "weak setup"


def test_distribution_calculates_requested_metrics() -> None:
    result = MongoHandler._distribution([1.0, 2.0, 3.0, 4.0])

    assert result["average"] == 2.5
    assert result["p95"] == pytest.approx(3.85)
    assert result["p99"] == pytest.approx(3.97)
    assert result["count"] == 4


def test_trade_metrics_do_not_embed_unbounded_sample_arrays() -> None:
    handler = MongoHandler.__new__(MongoHandler)
    handler.handle_exception = MagicMock()
    handler.trade_lifecycle_collection = MagicMock()
    handler.trade_metrics_collection = MagicMock()
    handler.trade_lifecycle_collection.find_one.return_value = {
        "metrics_status": "pending"
    }
    handler.trade_lifecycle_collection.find.return_value = [
        {"duration_seconds": 60.0, "pnl": 2.0},
        {"duration_seconds": 120.0, "pnl": -1.0},
    ]
    record = {
        "trade_id": "decision-2",
        "execution_mode": "testnet",
        "duration_seconds": 120.0,
        "pnl": -1.0,
    }

    assert handler.store_trade_exit(record) is True

    metrics_update = handler.trade_metrics_collection.update_one.call_args.args[1]
    assert metrics_update["$set"]["sample_count"] == 2
    assert metrics_update["$set"]["active_trade_duration_seconds"]["average"] == 90.0
    assert "$push" not in metrics_update
    assert "duration_samples" in metrics_update["$unset"]


def test_confirmed_exit_persists_llm_review_and_trade_metrics() -> None:
    checker = TradeChecker.__new__(TradeChecker)
    checker.trades = {"BTCUSDT": {"trade_id": "decision-1"}}
    checker.order_manager = MagicMock()
    checker.order_manager.get_symbol_price.return_value = 110.0
    exit_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    checker.order_manager.get_account_trades.return_value = [
        {
            "id": 1,
            "orderId": 10,
            "side": "BUY",
            "price": "100",
            "qty": "2",
            "time": exit_time_ms - 600_000,
            "commission": "1",
            "realizedPnl": "0",
        },
        {
            "id": 2,
            "orderId": 20,
            "side": "SELL",
            "price": "110",
            "qty": "2",
            "time": exit_time_ms,
            "commission": "1",
            "realizedPnl": "22",
        },
    ]
    checker.order_manager.future_client_for.return_value.get_income_history.return_value = [
        {
            "tranId": 1,
            "time": exit_time_ms,
            "symbol": "BTCUSDT",
            "incomeType": "REALIZED_PNL",
            "income": "22",
        },
        {
            "tranId": 2,
            "time": exit_time_ms,
            "symbol": "BTCUSDT",
            "incomeType": "COMMISSION",
            "income": "-2",
        },
    ]
    checker.execution_settings = ExecutionSettings({"BTCUSDT": ExecutionMode.TESTNET})
    checker.mongo_handler = MagicMock()
    checker._trade_reasoner = MagicMock()
    checker._trade_reasoner.review_exit.return_value = ExitReasoning(
        outcome="winning", reasoning="momentum continued", confidence=0.9
    )
    checker._position_is_flat = MagicMock(return_value=True)
    persisted = {
        "trade_id": "decision-1",
        "symbol": "BTCUSDT",
        "positionSide": "BUY",
        "price": 100.0,
        "quantity": 2.0,
        "orderId": 10,
    }
    checker.load_trade = MagicMock(return_value=persisted)
    checker.delete_trade_with_orders = MagicMock()
    checker.set_cooldown = MagicMock()

    assert checker._exit_trade("BTCUSDT", "decision-1") is True

    exit_record = checker.mongo_handler.store_trade_exit.call_args.args[0]
    assert exit_record["pnl"] == 20.0
    assert exit_record["pnl_source"] == "binance_trade_fills_and_funding"
    assert exit_record["duration_seconds"] >= 599
    assert exit_record["llm_exit_reasoning"]["reasoning"] == "momentum continued"
    checker.mongo_handler.append_decision_event.assert_called_once()


def test_exit_uses_income_commission_when_fill_commission_is_non_usdt() -> None:
    checker = TradeChecker.__new__(TradeChecker)
    checker.trades = {"BTCUSDT": {"trade_id": "decision-bnb-fee"}}
    checker.order_manager = MagicMock()
    exit_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    checker.order_manager.get_account_trades.return_value = [
        {
            "id": 1,
            "orderId": 10,
            "side": "BUY",
            "price": "100",
            "qty": "1",
            "time": exit_time_ms - 60_000,
            "commission": "0.001",
            "commissionAsset": "BNB",
            "realizedPnl": "0",
        },
        {
            "id": 2,
            "orderId": 20,
            "side": "SELL",
            "price": "110",
            "qty": "1",
            "time": exit_time_ms,
            "commission": "0.001",
            "commissionAsset": "BNB",
            "realizedPnl": "10",
        },
    ]
    checker.order_manager.future_client_for.return_value.get_income_history.return_value = [
        {
            "tranId": 1,
            "time": exit_time_ms,
            "symbol": "BTCUSDT",
            "incomeType": "COMMISSION",
            "income": "-0.60",
            "asset": "USDT",
        }
    ]
    checker.execution_settings = ExecutionSettings({"BTCUSDT": ExecutionMode.TESTNET})
    checker.mongo_handler = MagicMock()
    checker._trade_reasoner = MagicMock()
    checker._position_is_flat = MagicMock(return_value=True)
    checker.load_trade = MagicMock(
        return_value={
            "trade_id": "decision-bnb-fee",
            "symbol": "BTCUSDT",
            "positionSide": "BUY",
            "quantity": 1.0,
            "orderId": 10,
        }
    )
    checker.delete_trade_with_orders = MagicMock()
    checker.set_cooldown = MagicMock()

    assert checker._exit_trade("BTCUSDT", "decision-bnb-fee") is True

    exit_record = checker.mongo_handler.store_trade_exit.call_args.args[0]
    assert exit_record["pnl"] == pytest.approx(9.4)


def test_exit_refreshes_cooldown_before_failed_lifecycle_persistence() -> None:
    checker = TradeChecker.__new__(TradeChecker)
    checker.trades = {"BTCUSDT": {"trade_id": "decision-1"}}
    checker.order_manager = MagicMock()
    exit_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    checker.order_manager.get_account_trades.return_value = [
        {
            "id": 1,
            "orderId": 10,
            "side": "BUY",
            "price": "100",
            "qty": "1",
            "time": exit_time_ms - 60_000,
            "realizedPnl": "0",
        },
        {
            "id": 2,
            "orderId": 20,
            "side": "SELL",
            "price": "101",
            "qty": "1",
            "time": exit_time_ms,
            "realizedPnl": "1",
        },
    ]
    checker.order_manager.future_client_for.return_value.get_income_history.return_value = (
        []
    )
    checker.execution_settings = ExecutionSettings({"BTCUSDT": ExecutionMode.TESTNET})
    checker.mongo_handler = MagicMock()
    checker.mongo_handler.store_trade_exit.return_value = False
    checker._trade_reasoner = MagicMock()
    checker._position_is_flat = MagicMock(return_value=True)
    checker.load_trade = MagicMock(
        return_value={
            "trade_id": "decision-1",
            "symbol": "BTCUSDT",
            "positionSide": "BUY",
            "quantity": 1.0,
            "orderId": 10,
        }
    )
    checker.delete_trade_with_orders = MagicMock()
    checker.set_cooldown = MagicMock()

    with pytest.raises(RuntimeError, match="lifecycle persistence failed"):
        checker._exit_trade("BTCUSDT", "decision-1")

    checker.set_cooldown.assert_called_once_with("BTCUSDT")
    checker.delete_trade_with_orders.assert_not_called()


def test_exit_rejects_fills_after_a_new_entry_lifecycle() -> None:
    checker = TradeChecker.__new__(TradeChecker)
    checker.trades = {"BTCUSDT": {"trade_id": "stale"}}
    checker.order_manager = MagicMock()
    checker.order_manager.get_account_trades.return_value = [
        {"id": 1, "orderId": 10, "side": "BUY", "qty": "1", "time": 1000},
        {"id": 2, "orderId": 11, "side": "BUY", "qty": "1", "time": 2000},
        {"id": 3, "orderId": 12, "side": "SELL", "qty": "1", "time": 3000},
    ]
    checker.execution_settings = ExecutionSettings({"BTCUSDT": ExecutionMode.TESTNET})
    checker.mongo_handler = MagicMock()
    checker._position_is_flat = MagicMock(return_value=True)
    checker.load_trade = MagicMock(
        return_value={
            "trade_id": "stale",
            "symbol": "BTCUSDT",
            "positionSide": "BUY",
            "quantity": 1.0,
            "orderId": 10,
        }
    )
    checker.delete_trade_with_orders = MagicMock()

    with pytest.raises(RuntimeError, match="exit fills were ambiguous"):
        checker._exit_trade("BTCUSDT", "stale")

    checker.mongo_handler.store_trade_exit.assert_not_called()
    checker.delete_trade_with_orders.assert_not_called()


def test_reconstructed_exit_requires_closing_fills() -> None:
    checker = TradeChecker.__new__(TradeChecker)
    checker.trades = {"BTCUSDT": {"trade_id": "legacy"}}
    checker.order_manager = MagicMock()
    checker.order_manager.get_account_trades.return_value = []
    checker.execution_settings = ExecutionSettings({"BTCUSDT": ExecutionMode.TESTNET})
    checker.mongo_handler = MagicMock()
    checker._position_is_flat = MagicMock(return_value=True)
    checker.load_trade = MagicMock(
        return_value={
            "trade_id": "legacy",
            "symbol": "BTCUSDT",
            "positionSide": "BUY",
            "quantity": 1.0,
        }
    )

    with pytest.raises(RuntimeError, match="exit fills were unavailable"):
        checker._exit_trade("BTCUSDT", "legacy")

    checker.mongo_handler.store_trade_exit.assert_not_called()
