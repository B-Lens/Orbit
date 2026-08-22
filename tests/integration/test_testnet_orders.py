"""Submit and cancel one production-shaped Futures Testnet order per asset."""

from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import MagicMock
import uuid

import yaml

from config.config import load_config
from orbit.core.execution import ExecutionMode, ExecutionSettings, FUTURES_TESTNET_URL
from orbit.core.order_manager import OrderManager

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class TestAutomatedAssetConfiguration(unittest.TestCase):
    """Keep every automated asset compatible with the shared order path."""

    def test_every_trading_asset_has_complete_testnet_configuration(self) -> None:
        config = load_config()
        strategies = yaml.safe_load(
            (PROJECT_ROOT / "config" / "strategies.yaml").read_text(encoding="utf-8")
        )["strategies"]

        for symbol in config["trading_pairs"]:
            with self.subTest(symbol=symbol):
                self.assertIn(symbol, config["trading_pairs_precision"])
                self.assertIn(symbol, config["risk_management"])
                self.assertIn(symbol, config["FIXED_TRADE_AMOUNT"])
                self.assertIn(symbol, strategies)
                allowed_modes = strategies[symbol].get("execution_modes", [])
                self.assertTrue(
                    not allowed_modes or "testnet" in allowed_modes,
                    f"{symbol} strategy is not permitted on Testnet",
                )


@unittest.skipUnless(
    os.getenv("ORBIT_RUN_TESTNET_ORDER_TESTS", "").lower() == "true",
    "real Testnet orders require ORBIT_RUN_TESTNET_ORDER_TESTS=true",
)
class TestAllAssetTestnetOrders(unittest.TestCase):
    """Exercise the same sizing, risk, filter, and order code used by automation."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config()
        cls.symbols = list(cls.config["trading_pairs"])
        if not cls.symbols:
            raise AssertionError("No automated trading assets configured")

        testnet_url = os.getenv("BINANCE_FUTURES_TESTNET_URL", FUTURES_TESTNET_URL)
        if testnet_url.rstrip("/") != FUTURES_TESTNET_URL:
            raise AssertionError(
                "Integration orders are restricted to Binance Futures Testnet"
            )
        missing = [
            name
            for name in ("BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_SECRET_KEY")
            if not os.getenv(name)
        ]
        if missing:
            raise AssertionError(f"Missing required CI secrets: {', '.join(missing)}")

        settings = ExecutionSettings(
            {symbol: ExecutionMode.TESTNET for symbol in cls.symbols}
        )
        cls.mongo = MagicMock()
        cls.manager = OrderManager(
            mongo_handler=cls.mongo,
            redis_client=MagicMock(),
            config=cls.config,
            execution_settings=settings,
        )
        cls.manager.send_alerts = MagicMock()
        cls.manager.send_logs = MagicMock()
        cls.manager.send_signal_updates = MagicMock()

    def test_submit_and_cancel_limit_order_for_every_asset(self) -> None:
        for symbol in self.symbols:
            with self.subTest(symbol=symbol):
                self.assertIs(
                    self.manager.execution_settings.mode_for(symbol),
                    ExecutionMode.TESTNET,
                )
                market_price = self.manager.get_symbol_price(symbol)
                entry_price = self.manager.adjust_price_tick(symbol, market_price * 0.5)
                stop_loss = entry_price * 0.99
                take_profit = entry_price * 1.02
                decision_id = f"ci-{symbol.lower()}-{uuid.uuid4().hex[:12]}"
                order_id = None

                try:
                    response, quantity, request = self.manager.place_order(
                        self.config["risk_management"],
                        symbol,
                        "BUY",
                        price=entry_price,
                        sl=stop_loss,
                        target=take_profit,
                        leverage=int(self.config["FUTURE_LEVERAGE"]),
                        ros=True,
                        trade_id=decision_id,
                    )
                    rejection_events = [
                        call.args[1]
                        for call in self.mongo.append_decision_event.call_args_list
                        if call.args and call.args[0] == decision_id
                    ]
                    self.assertIsNotNone(
                        response,
                        f"{symbol} Testnet order failed; decision events={rejection_events}",
                    )
                    assert response is not None
                    order_id = response.get("orderId")
                    self.assertIsNotNone(order_id, f"{symbol} response omitted orderId")
                    self.assertGreater(float(quantity or 0), 0)
                    self.assertIsNotNone(request)
                    assert request is not None
                    self.assertEqual(request["symbol"], symbol)
                    self.assertEqual(str(response.get("symbol", symbol)), symbol)
                finally:
                    if order_id is not None:
                        cancelled = self.manager.cancel_order(symbol, int(order_id))
                        self.assertIsNotNone(
                            cancelled,
                            f"Failed to cancel Testnet order {order_id} for {symbol}",
                        )


if __name__ == "__main__":
    unittest.main()
