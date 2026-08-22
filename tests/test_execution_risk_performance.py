import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from orbit.core.authentication_manager import AuthenticationManager
from orbit.core.execution import ExecutionMode, ExecutionSettings
from orbit.core.performance import PerformanceTracker
from orbit.core.risk_manager import PreTradeRiskGuard


class TestExecutionSettings(unittest.TestCase):
    def test_only_testnet_and_live_modes_exist(self):
        self.assertEqual(
            set(ExecutionMode),
            {ExecutionMode.TESTNET, ExecutionMode.LIVE},
        )

    def test_all_configured_assets_use_testnet(self):
        env = {
            "BINANCE_TESTNET_API_KEY": "key",
            "BINANCE_TESTNET_SECRET_KEY": "secret",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = ExecutionSettings.from_config()
        self.assertTrue(settings.can_submit_orders)
        self.assertEqual(
            set(settings.asset_modes),
            {
                "BTCUSDT",
                "ETHUSDT",
                "BCHUSDT",
                "PAXGUSDT",
                "BNBUSDT",
                "MKRUSDT",
                "LTCUSDT",
                "SOLUSDT",
                "ATOMUSDT",
                "XRPUSDT",
            },
        )
        self.assertEqual(set(settings.asset_modes.values()), {ExecutionMode.TESTNET})

    def test_missing_mode_is_rejected(self):
        with (
            patch.object(
                Path,
                "read_text",
                return_value="strategies:\n  BCHUSDT:\n    strategy: example.Strategy\n",
            ),
            self.assertRaisesRegex(ValueError, "execution_mode: testnet or live"),
        ):
            ExecutionSettings.from_config("strategies.yaml")

    def test_live_mode_is_accepted_with_live_credentials(self):
        env = {
            "BINANCE_API_KEY": "key",
            "BINANCE_SECRET_KEY": "secret",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(
                Path,
                "read_text",
                return_value=(
                    "strategies:\n  BCHUSDT:\n    strategy: example.Strategy\n"
                    "    execution_mode: live\n"
                ),
            ),
        ):
            settings = ExecutionSettings.from_config("strategies.yaml")
        self.assertEqual(settings.mode_for("BCHUSDT"), ExecutionMode.LIVE)

    def test_paper_mode_is_rejected(self):
        with (
            patch.object(
                Path,
                "read_text",
                return_value=(
                    "strategies:\n  BCHUSDT:\n    strategy: example.Strategy\n"
                    "    execution_mode: paper\n"
                ),
            ),
            self.assertRaisesRegex(ValueError, "only testnet or live is allowed"),
        ):
            ExecutionSettings.from_config("strategies.yaml")

    def test_testnet_credentials_are_required(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "testnet credentials"):
                ExecutionSettings.from_config()

    def test_live_credentials_are_required(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(
                Path,
                "read_text",
                return_value=(
                    "strategies:\n  BCHUSDT:\n    strategy: example.Strategy\n"
                    "    execution_mode: live\n"
                ),
            ),
            self.assertRaisesRegex(RuntimeError, "live credentials"),
        ):
            ExecutionSettings.from_config("strategies.yaml")

    def test_futures_client_is_routed_by_asset_mode(self):
        testnet_client = MagicMock()
        settings = ExecutionSettings(
            {"BCHUSDT": ExecutionMode.TESTNET},
        )
        manager = AuthenticationManager(
            spot_client=MagicMock(),
            futures_clients={
                ExecutionMode.TESTNET: testnet_client,
            },
            execution_settings=settings,
        )

        self.assertIs(manager.future_client_for("BCHUSDT"), testnet_client)
        with self.assertRaisesRegex(ValueError, "No execution mode configured"):
            manager.future_client_for("BTCUSDT")

    def test_live_asset_is_routed_only_to_live_client(self):
        live_client = MagicMock()
        settings = ExecutionSettings(
            {"BCHUSDT": ExecutionMode.LIVE},
        )
        manager = AuthenticationManager(
            spot_client=MagicMock(),
            futures_clients={
                ExecutionMode.LIVE: live_client,
            },
            execution_settings=settings,
        )

        self.assertIs(manager.future_client_for("BCHUSDT"), live_client)
        self.assertEqual(manager.futures_clients, {ExecutionMode.LIVE: live_client})

    def test_unknown_asset_configuration_is_rejected(self):
        settings = ExecutionSettings(
            {"DOGEUSDT": ExecutionMode.TESTNET},
        )
        with self.assertRaises(ValueError):
            AuthenticationManager(
                spot_client=MagicMock(),
                futures_client=MagicMock(),
                execution_settings=settings,
            )

    def test_trading_asset_without_configured_mode_is_rejected(self):
        settings = ExecutionSettings(
            {"BCHUSDT": ExecutionMode.TESTNET},
        )
        with (
            patch.object(ExecutionSettings, "from_config", return_value=settings),
            self.assertRaisesRegex(ValueError, "missing strategy execution modes"),
        ):
            AuthenticationManager(
                spot_client=MagicMock(),
                futures_clients={
                    ExecutionMode.TESTNET: MagicMock(),
                },
                config={
                    "trading_pairs": ["BCHUSDT", "PAXGUSDT"],
                    "trade_checker_pair": ["BCHUSDT", "PAXGUSDT"],
                },
            )


class TestRiskGuard(unittest.TestCase):
    def setUp(self):
        self.guard = PreTradeRiskGuard(
            {
                "max_leverage": 3,
                "max_position_notional_pct": 0.5,
                "max_risk_per_trade_pct": 0.01,
                "max_daily_loss_pct": 0.02,
                "min_reward_risk_ratio": 1.5,
            }
        )

    def test_accepts_bounded_trade(self):
        result = self.guard.evaluate(
            equity=1000,
            entry_price=100,
            stop_loss=98,
            take_profit=104,
            quantity=2,
            leverage=2,
            side="BUY",
        )
        self.assertTrue(result.allowed)

    def test_rejects_daily_loss_limit(self):
        result = self.guard.evaluate(
            equity=1000,
            entry_price=100,
            stop_loss=98,
            take_profit=104,
            quantity=2,
            leverage=2,
            side="BUY",
            daily_net_pnl=-20,
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "daily_loss_limit")

    def test_rejects_poor_reward_risk(self):
        result = self.guard.evaluate(
            equity=1000,
            entry_price=100,
            stop_loss=98,
            take_profit=101,
            quantity=2,
            leverage=2,
            side="BUY",
        )
        self.assertEqual(result.reason, "reward_risk_below_minimum")

    def test_rejects_stop_on_wrong_side(self):
        result = self.guard.evaluate(
            equity=1000,
            entry_price=100,
            stop_loss=101,
            take_profit=104,
            quantity=2,
            leverage=2,
            side="BUY",
        )
        self.assertEqual(result.reason, "stop_on_wrong_side")


class TestPerformanceTracker(unittest.TestCase):
    def test_net_return_includes_fees_and_funding(self):
        records = [
            {"incomeType": "REALIZED_PNL", "income": "25"},
            {"incomeType": "COMMISSION", "income": "-2"},
            {"incomeType": "FUNDING_FEE", "income": "-1"},
        ]
        summary = PerformanceTracker.summarize(records, starting_equity=1000)
        self.assertEqual(summary.net_pnl, 22)
        self.assertAlmostEqual(summary.return_pct, 2.2)

    def test_sync_tags_income_with_execution_mode(self):
        client = MagicMock()
        client.get_income_history.return_value = [
            {"tranId": 1, "incomeType": "REALIZED_PNL", "income": "2"}
        ]
        mongo = MagicMock()

        PerformanceTracker(client, mongo, "testnet").sync(123)

        mongo.store_income_records.assert_called_once_with(
            client.get_income_history.return_value, "testnet"
        )

    def test_sync_window_paginates_and_bounds_income(self):
        client = MagicMock()
        first_page = [
            {"tranId": 1, "time": 100, "incomeType": "COMMISSION", "income": "-1"},
            {"tranId": 2, "time": 101, "incomeType": "COMMISSION", "income": "-1"},
        ]
        second_page = [
            first_page[1],
            {"tranId": 3, "time": 102, "incomeType": "COMMISSION", "income": "-1"},
        ]
        client.get_income_history.side_effect = [
            first_page,
            second_page,
            [second_page[1]],
        ]
        mongo = MagicMock()

        summary = PerformanceTracker(client, mongo, "testnet").sync_window(
            100, 500, page_size=2
        )

        self.assertEqual(summary.records, 3)
        self.assertEqual(client.get_income_history.call_count, 3)
        self.assertEqual(
            client.get_income_history.call_args_list[1].kwargs["startTime"], 101
        )
        self.assertEqual(
            client.get_income_history.call_args_list[2].kwargs["startTime"], 102
        )
        self.assertEqual(
            client.get_income_history.call_args_list[0].kwargs["endTime"], 499
        )

    def test_sync_window_fails_closed_on_unpageable_timestamp(self):
        client = MagicMock()
        full_page = [
            {"tranId": 1, "time": 100, "incomeType": "COMMISSION", "income": "-1"}
        ]
        client.get_income_history.side_effect = [full_page, full_page]

        with self.assertRaisesRegex(RuntimeError, "refusing to publish incomplete"):
            PerformanceTracker(client, MagicMock(), "testnet").sync_window(
                100, 500, page_size=1
            )


if __name__ == "__main__":
    unittest.main()
