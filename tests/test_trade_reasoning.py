from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event
from unittest.mock import MagicMock, patch

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


def test_core_uses_configured_leverage_for_every_asset() -> None:
    order_manager = MagicMock()
    order_manager.place_order.return_value = (None, None, None)
    trade_reasoner = MagicMock()
    trade_reasoner.review_entry.return_value = EntryReasoning(True, "aligned", 0.9)
    automation = BinanceAutomation.__new__(BinanceAutomation)
    automation.order_manager = order_manager
    automation._trade_reasoner = trade_reasoner
    automation.future_leverage = 5
    automation.risk_management = {}
    automation.send_logs = MagicMock()
    automation.send_alerts = MagicMock()

    signal = {**_signal(), "symbol": "ETHUSDT"}
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("orbit.core.main.time.sleep", lambda _seconds: None)
        automation.process_signal(signal)

    assert order_manager.place_order.call_args.args[6] == 5


def test_entry_order_is_serialized_with_position_cleanup() -> None:
    events = []

    @contextmanager
    def lifecycle_lock():
        events.append("lock_entered")
        yield
        events.append("lock_released")

    order_manager = MagicMock()
    order_manager.place_order.side_effect = lambda *_args, **_kwargs: (
        events.append("order_placed") or (None, None, None)
    )
    trade_reasoner = MagicMock()
    trade_reasoner.review_entry.return_value = EntryReasoning(True, "aligned", 0.9)
    automation = BinanceAutomation.__new__(BinanceAutomation)
    automation.order_manager = order_manager
    automation._trade_reasoner = trade_reasoner
    automation.future_leverage = 5
    automation.risk_management = {}
    automation.send_logs = MagicMock()
    automation.send_alerts = MagicMock()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "orbit.core.main.position_lifecycle_lock",
            lambda symbol, redis_client=None: lifecycle_lock(),
        )
        monkeypatch.setattr("orbit.core.main.time.sleep", lambda _seconds: None)
        automation.process_signal(_signal())

    assert events == ["lock_entered", "order_placed", "lock_released"]


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
    exit_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - 120_000
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
    assert exit_record["closed_at"] == datetime.fromtimestamp(
        exit_time_ms / 1000, tz=timezone.utc
    )
    assert exit_record["duration_seconds"] >= 599
    assert exit_record["llm_exit_reasoning"]["reasoning"] == "momentum continued"
    checker.mongo_handler.append_decision_event.assert_called_once()
    close_event = checker.mongo_handler.append_decision_event.call_args.args[1]
    assert close_event["timestamp"] == datetime.fromtimestamp(
        exit_time_ms / 1000, tz=timezone.utc
    )


def test_exit_uses_income_commission_when_fill_commission_is_non_usdt() -> None:
    checker = TradeChecker.__new__(TradeChecker)
    checker.trades = {"BTCUSDT": {"trade_id": "decision-bnb-fee"}}
    checker.order_manager = MagicMock()
    exit_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - 120_000
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


def test_exit_preserves_trade_state_when_close_event_persistence_fails() -> None:
    checker = TradeChecker.__new__(TradeChecker)
    checker.trades = {"BTCUSDT": {"trade_id": "decision-1"}}
    checker.order_manager = MagicMock()
    exit_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - 120_000
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
    checker.order_manager.future_client_for.return_value.get_income_history.return_value = [
        {
            "tranId": 1,
            "time": exit_time_ms,
            "symbol": "BTCUSDT",
            "incomeType": "REALIZED_PNL",
            "income": "1",
        }
    ]
    checker.execution_settings = ExecutionSettings({"BTCUSDT": ExecutionMode.TESTNET})
    checker.mongo_handler = MagicMock()
    checker.mongo_handler.store_trade_exit.return_value = True
    checker.mongo_handler.append_decision_event.return_value = False
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

    with pytest.raises(RuntimeError, match="close event persistence failed"):
        checker._exit_trade("BTCUSDT", "decision-1")

    checker.set_cooldown.assert_called_once_with("BTCUSDT")
    checker.delete_trade_with_orders.assert_not_called()


def test_exit_retry_reuses_durable_lifecycle_record() -> None:
    checker = TradeChecker.__new__(TradeChecker)
    checker.trades = {"BTCUSDT": {"trade_id": "decision-1"}}
    checker.order_manager = MagicMock()
    checker.execution_settings = ExecutionSettings({"BTCUSDT": ExecutionMode.TESTNET})
    closed_at = datetime.now(timezone.utc)
    checker.mongo_handler = MagicMock()
    checker.mongo_handler.get_trade_exit.return_value = {
        "trade_id": "decision-1",
        "closed_at": closed_at,
        "exit_price": 101.0,
        "pnl": 1.0,
        "duration_seconds": 60.0,
        "llm_exit_reasoning": {"reasoning": "stored review"},
    }
    checker.mongo_handler.append_decision_event.return_value = True
    checker._position_is_flat = MagicMock(return_value=True)
    checker.load_trade = MagicMock(
        return_value={
            "trade_id": "decision-1",
            "symbol": "BTCUSDT",
            "positionSide": "BUY",
            "quantity": 1.0,
        }
    )
    checker.delete_trade_with_orders = MagicMock()
    checker.set_cooldown = MagicMock()

    assert checker._exit_trade("BTCUSDT", "decision-1") is True

    checker.order_manager.get_account_trades.assert_not_called()
    checker.delete_trade_with_orders.assert_called_once_with("decision-1")
    close_event = checker.mongo_handler.append_decision_event.call_args.args[1]
    assert close_event["timestamp"] == closed_at
    assert close_event["llm_exit_reasoning"] == {"reasoning": "stored review"}


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
    checker.set_cooldown = MagicMock()

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
            "entry_source": "broker_reconstruction",
        }
    )
    checker.set_cooldown = MagicMock()
    checker.merge_existing_trade_fields = MagicMock(return_value=True)

    with patch("orbit.core.trade_checker.uuid4", return_value="unique"):
        with pytest.raises(RuntimeError, match="exit fills were unavailable"):
            checker._exit_trade("BTCUSDT", "legacy")

    checker.merge_existing_trade_fields.assert_called_once_with(
        "legacy", {"lifecycle_id": "reconstructed:BTCUSDT:unique"}
    )
    checker.mongo_handler.get_trade_exit.assert_called_once_with(
        "reconstructed:BTCUSDT:unique"
    )


def test_reconstructed_exit_groups_split_entry_fills_and_stores_exit_price() -> None:
    checker = TradeChecker.__new__(TradeChecker)
    checker.trades = {"SKYUSDT": {"trade_id": "SKYUSDT"}}
    checker.order_manager = MagicMock()
    exit_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - 120_000
    checker.order_manager.get_account_trades.return_value = [
        {
            "id": 1,
            "orderId": 162370722,
            "side": "SELL",
            "price": "0.06904",
            "qty": "5000",
            "time": exit_time_ms - 600_000,
            "realizedPnl": "0",
        },
        {
            "id": 2,
            "orderId": 162370722,
            "side": "SELL",
            "price": "0.06906",
            "qty": "6000",
            "time": exit_time_ms - 599_000,
            "realizedPnl": "0",
        },
        {
            "id": 3,
            "orderId": 163130012,
            "side": "BUY",
            "price": "0.06471",
            "qty": "11000",
            "time": exit_time_ms,
            "realizedPnl": "47.74",
        },
    ]
    checker.order_manager.future_client_for.return_value.get_income_history.return_value = [
        {
            "tranId": 1,
            "time": exit_time_ms,
            "symbol": "SKYUSDT",
            "incomeType": "REALIZED_PNL",
            "income": "47.74",
        }
    ]
    checker.execution_settings = ExecutionSettings({"SKYUSDT": ExecutionMode.TESTNET})
    checker.mongo_handler = MagicMock()
    checker.mongo_handler.store_trade_exit.return_value = True
    checker._trade_reasoner = MagicMock()
    checker._trade_reasoner.review_exit.return_value = ExitReasoning(
        outcome="winning", reasoning="target filled", confidence=1.0
    )
    checker._position_is_flat = MagicMock(return_value=True)
    checker.load_trade = MagicMock(
        return_value={
            "trade_id": "SKYUSDT",
            "lifecycle_id": "reconstructed:SKYUSDT:unique",
            "symbol": "SKYUSDT",
            "positionSide": "SELL",
            "quantity": 11000,
            "entry_source": "broker_reconstruction",
            "entered_at": datetime.fromtimestamp(
                (exit_time_ms - 900_000) / 1000, tz=timezone.utc
            ).isoformat(),
        }
    )
    checker.delete_trade_with_orders = MagicMock()
    checker.set_cooldown = MagicMock()

    assert checker._exit_trade("SKYUSDT", "SKYUSDT") is True

    exit_record = checker.mongo_handler.store_trade_exit.call_args.args[0]
    assert exit_record["trade_id"] == "reconstructed:SKYUSDT:unique"
    assert exit_record["redis_trade_id"] == "SKYUSDT"
    assert exit_record["exit_price"] == pytest.approx(0.06471)
    assert exit_record["closed_at"] == datetime.fromtimestamp(
        exit_time_ms / 1000, tz=timezone.utc
    )
    checker.mongo_handler.append_decision_event.assert_not_called()
    checker.delete_trade_with_orders.assert_called_once_with("SKYUSDT")


def test_reconstructed_exit_rejects_multiple_entry_order_ids() -> None:
    checker = TradeChecker.__new__(TradeChecker)
    checker.order_manager = MagicMock()
    checker.order_manager.get_account_trades.return_value = [
        {"id": 1, "orderId": 10, "side": "SELL", "qty": "5000", "time": 1000},
        {"id": 2, "orderId": 11, "side": "SELL", "qty": "6000", "time": 2000},
        {"id": 3, "orderId": 20, "side": "BUY", "qty": "11000", "time": 3000},
    ]
    checker.execution_settings = ExecutionSettings({"SKYUSDT": ExecutionMode.TESTNET})
    checker.mongo_handler = MagicMock()
    checker._position_is_flat = MagicMock(return_value=True)
    checker.load_trade = MagicMock(
        return_value={
            "trade_id": "SKYUSDT",
            "lifecycle_id": "reconstructed:SKYUSDT:unique",
            "symbol": "SKYUSDT",
            "positionSide": "SELL",
            "quantity": 11000,
            "entry_source": "broker_reconstruction",
            "entered_at": "1970-01-01T00:00:00+00:00",
        }
    )
    checker.set_cooldown = MagicMock()

    with pytest.raises(RuntimeError, match="exit fills were ambiguous"):
        checker._exit_trade("SKYUSDT", "SKYUSDT")

    checker.mongo_handler.store_trade_exit.assert_not_called()


def test_exit_reconciliation_is_serialized_per_symbol() -> None:
    checker = TradeChecker.__new__(TradeChecker)
    first_entered = Event()
    release_first = Event()
    second_finished = Event()
    call_count = 0

    def reconcile(_symbol: str, _trade_id: str) -> bool:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            first_entered.set()
            assert release_first.wait(timeout=1)
        else:
            second_finished.set()
        return True

    checker._exit_trade_locked = reconcile

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(checker._exit_trade, "SKYUSDT", "SKYUSDT")
        assert first_entered.wait(timeout=1)
        second = executor.submit(checker._exit_trade, "SKYUSDT", "SKYUSDT")
        assert not second_finished.wait(timeout=0.05)
        release_first.set()
        assert first.result(timeout=1) is True
        assert second.result(timeout=1) is True

    assert second_finished.is_set()


def test_exit_defers_cleanup_during_income_settlement_grace_period() -> None:
    checker = TradeChecker.__new__(TradeChecker)
    checker.trades = {"BTCUSDT": {"trade_id": "settling-income"}}
    checker.order_manager = MagicMock()
    exit_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    checker.order_manager.get_account_trades.return_value = [
        {
            "id": 1,
            "orderId": 10,
            "side": "BUY",
            "qty": "1",
            "time": exit_time_ms - 1000,
        },
        {
            "id": 2,
            "orderId": 20,
            "side": "SELL",
            "qty": "1",
            "time": exit_time_ms,
            "price": "101",
        },
    ]
    checker.execution_settings = ExecutionSettings({"BTCUSDT": ExecutionMode.TESTNET})
    checker.mongo_handler = MagicMock()
    checker._position_is_flat = MagicMock(return_value=True)
    checker.load_trade = MagicMock(
        return_value={
            "trade_id": "settling-income",
            "symbol": "BTCUSDT",
            "positionSide": "BUY",
            "quantity": 1.0,
            "orderId": 10,
        }
    )
    checker.set_cooldown = MagicMock()
    checker.delete_trade_with_orders = MagicMock()

    assert checker._exit_trade("BTCUSDT", "settling-income") is False

    checker.order_manager.future_client_for.assert_not_called()
    checker.mongo_handler.store_trade_exit.assert_not_called()
    checker.delete_trade_with_orders.assert_not_called()


def test_exit_defers_cleanup_when_income_history_is_empty() -> None:
    checker = TradeChecker.__new__(TradeChecker)
    checker.trades = {"BTCUSDT": {"trade_id": "delayed-income"}}
    checker.order_manager = MagicMock()
    exit_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - 120_000
    checker.order_manager.get_account_trades.return_value = [
        {
            "id": 1,
            "orderId": 10,
            "side": "BUY",
            "qty": "1",
            "time": exit_time_ms - 1000,
        },
        {
            "id": 2,
            "orderId": 20,
            "side": "SELL",
            "qty": "1",
            "time": exit_time_ms,
            "price": "101",
        },
    ]
    checker.order_manager.future_client_for.return_value.get_income_history.return_value = (
        []
    )
    checker.execution_settings = ExecutionSettings({"BTCUSDT": ExecutionMode.TESTNET})
    checker.mongo_handler = MagicMock()
    checker._position_is_flat = MagicMock(return_value=True)
    checker.load_trade = MagicMock(
        return_value={
            "trade_id": "delayed-income",
            "symbol": "BTCUSDT",
            "positionSide": "BUY",
            "quantity": 1.0,
            "orderId": 10,
        }
    )
    checker.set_cooldown = MagicMock()
    checker.delete_trade_with_orders = MagicMock()

    assert checker._exit_trade("BTCUSDT", "delayed-income") is False

    checker.mongo_handler.store_trade_exit.assert_not_called()
    checker.delete_trade_with_orders.assert_not_called()
