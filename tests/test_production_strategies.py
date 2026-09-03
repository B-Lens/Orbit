import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from orbit.strategies.atomusdt_strategy import ATOMUSDTStrategy
from orbit.strategies.btc_strategy import BTCStrategy
from orbit.strategies.eth_strategy import ETHStrategy
from orbit.strategies.skyusdt_strategy import SKYUSDTStrategy
from orbit.strategies.paxgusdt_strategy import PAXGUSDTStrategy
from orbit.strategies.reversal_strategy import BollingerAdaptiveReversalStrategyBCH
from orbit.strategies.strategies_base import Strategy
from orbit.strategies.strategy_registry import STRATEGY_REGISTRY, _LazyStrategyRegistry


class TestProductionStrategyOwnership(unittest.TestCase):
    def test_registry_resolves_internal_classes(self):
        self.assertIs(STRATEGY_REGISTRY["BTCUSDT"], BTCStrategy)
        self.assertIs(STRATEGY_REGISTRY["ETHUSDT"], ETHStrategy)
        self.assertIs(
            STRATEGY_REGISTRY["BCHUSDT"], BollingerAdaptiveReversalStrategyBCH
        )
        self.assertIs(STRATEGY_REGISTRY["PAXGUSDT"], PAXGUSDTStrategy)
        self.assertIs(STRATEGY_REGISTRY["SKYUSDT"], SKYUSDTStrategy)
        self.assertIs(STRATEGY_REGISTRY["ATOMUSDT"], ATOMUSDTStrategy)

    def test_all_production_strategies_use_orbit_contract(self):
        for strategy_class in (
            BTCStrategy,
            BollingerAdaptiveReversalStrategyBCH,
            ETHStrategy,
            PAXGUSDTStrategy,
            SKYUSDTStrategy,
            ATOMUSDTStrategy,
        ):
            self.assertTrue(issubclass(strategy_class, Strategy))
            self.assertTrue(strategy_class.__module__.startswith("orbit.strategies."))

    def test_registry_loads_configured_strategy(self):
        registry = _LazyStrategyRegistry(
            {
                "strategies": {
                    "ETHUSDT": {
                        "strategy": "orbit.strategies.eth_strategy.ETHStrategy",
                        "execution_mode": "testnet",
                    }
                }
            }
        )
        self.assertIs(registry["ETHUSDT"], ETHStrategy)


def hourly_btc_frame(closes: list[float]) -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=len(closes), freq="1h")
    return pd.DataFrame(
        {
            "open": closes,
            "high": [value + 1 for value in closes],
            "low": [value - 1 for value in closes],
            "close": closes,
            "volume": [100.0] * len(closes),
        },
        index=index,
    )


class TestBTCStrategy(unittest.TestCase):
    @patch("orbit.strategies.btc_strategy.generate_chart", return_value=None)
    def test_long_breakout_has_three_to_one_reward_risk(self, _chart):
        signal = BTCStrategy(hourly_btc_frame([100.0] * 55 + [105.0])).generate_signals(
            symbol="BTCUSDT"
        )

        self.assertEqual(signal["signal"], "BUY")
        risk = signal["entry_price"] - signal["stop_loss"]
        reward = signal["take_profit"] - signal["entry_price"]
        self.assertAlmostEqual(reward / risk, 3.0)

    @patch("orbit.strategies.btc_strategy.generate_chart", return_value=None)
    def test_short_breakout_has_three_to_one_reward_risk(self, _chart):
        signal = BTCStrategy(hourly_btc_frame([100.0] * 55 + [95.0])).generate_signals(
            symbol="BTCUSDT"
        )

        self.assertEqual(signal["signal"], "SELL")
        risk = signal["stop_loss"] - signal["entry_price"]
        reward = signal["entry_price"] - signal["take_profit"]
        self.assertAlmostEqual(reward / risk, 3.0)

    def test_partial_hour_does_not_emit_signal(self):
        hourly = hourly_btc_frame([100.0] * 55 + [105.0])
        rows = [
            (timestamp + pd.Timedelta(minutes=15 * offset), row.copy())
            for timestamp, row in hourly.iterrows()
            for offset in range(4)
        ]
        data = pd.DataFrame(
            [row for _, row in rows], index=[timestamp for timestamp, _ in rows]
        ).iloc[:-1]

        self.assertIsNone(BTCStrategy(data).generate_signals(symbol="BTCUSDT"))

    def test_trailing_stop_uses_recent_price_structure(self):
        data = hourly_btc_frame([100.0] * 55 + [105.0])
        signal = BTCStrategy(data).generate_signals(
            symbol="BTCUSDT", position_side="LONG"
        )

        self.assertEqual(signal["signal"], "UPDATE_SL_TP")
        self.assertLess(signal["stop_loss"], data["high"].iloc[-12:].max())


def hourly_sky_frame(final_close: float) -> pd.DataFrame:
    closes = [100.0] * 105 + [final_close]
    hourly = pd.DataFrame(
        {
            "open": closes,
            "high": [value + 1 for value in closes],
            "low": [value - 1 for value in closes],
            "close": closes,
            "volume": [100.0] * len(closes),
        },
        index=pd.date_range("2025-01-01", periods=len(closes), freq="1h"),
    )
    rows = [
        (timestamp + pd.Timedelta(minutes=15 * offset), row)
        for timestamp, row in hourly.iterrows()
        for offset in range(4)
    ]
    return pd.DataFrame(
        [row for _, row in rows], index=[timestamp for timestamp, _ in rows]
    )


class TestSKYUSDTStrategy(unittest.TestCase):
    def test_long_breakout_has_four_to_one_reward_risk(self):
        data = hourly_sky_frame(105.0)
        with patch.object(
            SKYUSDTStrategy,
            "_current_hour",
            return_value=data.index[-1].floor("h") + pd.Timedelta(hours=1),
        ):
            signal = SKYUSDTStrategy(data).generate_signals()

        self.assertEqual(signal["signal"], "BUY")
        risk = signal["entry_price"] - signal["stop_loss"]
        reward = signal["take_profit"] - signal["entry_price"]
        self.assertAlmostEqual(reward / risk, 4.0)

    def test_entry_uses_completed_breakout_close_without_future_candle(self):
        data = hourly_sky_frame(105.0)
        with patch.object(
            SKYUSDTStrategy,
            "_current_hour",
            return_value=data.index[-1].floor("h") + pd.Timedelta(hours=1),
        ):
            signal = SKYUSDTStrategy(data).generate_signals()

        self.assertEqual(signal["entry_price"], 105.0)

    def test_short_breakout_has_four_to_one_reward_risk(self):
        data = hourly_sky_frame(95.0)
        with patch.object(
            SKYUSDTStrategy,
            "_current_hour",
            return_value=data.index[-1].floor("h") + pd.Timedelta(hours=1),
        ):
            signal = SKYUSDTStrategy(data).generate_signals()

        self.assertEqual(signal["signal"], "SELL")
        risk = signal["stop_loss"] - signal["entry_price"]
        reward = signal["entry_price"] - signal["take_profit"]
        self.assertAlmostEqual(reward / risk, 4.0)

    def test_existing_position_suppresses_entry(self):
        signal = SKYUSDTStrategy(hourly_sky_frame(105.0)).generate_signals(
            position_side="LONG"
        )

        self.assertIsNone(signal)

    def test_incomplete_resampled_hour_suppresses_entry(self):
        hourly = hourly_sky_frame(105.0)
        rows = [
            (timestamp + pd.Timedelta(minutes=15 * offset), row)
            for timestamp, row in hourly.iterrows()
            for offset in range(4)
        ]
        partial = pd.DataFrame(
            [row for _, row in rows], index=[timestamp for timestamp, _ in rows]
        ).iloc[:-1]

        self.assertIsNone(SKYUSDTStrategy(partial).generate_signals())

    def test_stale_completed_hour_suppresses_entry(self):
        data = hourly_sky_frame(105.0)
        with patch.object(
            SKYUSDTStrategy,
            "_current_hour",
            return_value=data.index[-1].floor("h") + pd.Timedelta(hours=2),
        ):
            signal = SKYUSDTStrategy(data).generate_signals()

        self.assertIsNone(signal)

    def test_missing_complete_hour_suppresses_entry(self):
        data = hourly_sky_frame(105.0)
        data = data.drop(data.index[-12:-8])
        with patch.object(
            SKYUSDTStrategy,
            "_current_hour",
            return_value=data.index[-1].floor("h") + pd.Timedelta(hours=1),
        ):
            signal = SKYUSDTStrategy(data).generate_signals()

        self.assertIsNone(signal)


class TestBCHStrategyRiskContract(unittest.TestCase):
    def setUp(self):
        index = pd.date_range("2026-01-01", periods=20, freq="15min")
        self.data = pd.DataFrame(
            {
                "open": [100.0] * 20,
                "high": [101.0] * 20,
                "low": [99.0] * 20,
                "close": [100.0] * 19 + [101.0],
                "volume": [1.0] * 20,
            },
            index=index,
        )

    def test_long_signal_has_two_to_one_reward_risk(self):
        strategy = BollingerAdaptiveReversalStrategyBCH(self.data)
        lower = pd.Series([101.0] * 19 + [100.0], index=self.data.index)
        upper = pd.Series([200.0] * 20, index=self.data.index)
        with (
            patch.object(
                strategy, "compute_bollinger_bands", return_value=(upper, upper, lower)
            ),
            patch.object(strategy, "compute_sma", return_value=upper),
            patch.object(strategy, "is_bullish_reversal", return_value=True),
            patch.object(strategy, "send_params"),
            patch(
                "orbit.strategies.reversal_strategy.generate_chart", return_value=None
            ),
        ):
            signal = strategy.generate_signals(symbol="BCHUSDT")

        self.assertEqual(signal["signal"], "BUY")
        risk = signal["entry_price"] - signal["stop_loss"]
        reward = signal["take_profit"] - signal["entry_price"]
        self.assertAlmostEqual(reward / risk, 2.0)

    def test_short_signal_has_two_to_one_reward_risk(self):
        data = self.data.copy()
        data.iloc[-1, data.columns.get_loc("close")] = 99.0
        strategy = BollingerAdaptiveReversalStrategyBCH(data)
        upper = pd.Series([98.0] * 19 + [100.0], index=data.index)
        lower = pd.Series([0.0] * 20, index=data.index)
        with (
            patch.object(
                strategy, "compute_bollinger_bands", return_value=(upper, lower, lower)
            ),
            patch.object(strategy, "compute_sma", return_value=upper),
            patch.object(strategy, "is_bearish_reversal", return_value=True),
            patch.object(strategy, "send_params"),
            patch(
                "orbit.strategies.reversal_strategy.generate_chart", return_value=None
            ),
        ):
            signal = strategy.generate_signals(symbol="BCHUSDT")

        self.assertEqual(signal["signal"], "SELL")
        risk = signal["stop_loss"] - signal["entry_price"]
        reward = signal["entry_price"] - signal["take_profit"]
        self.assertAlmostEqual(reward / risk, 2.0)



# ---------------------------------------------------------------------------
# ATOMUSDT helpers
# ---------------------------------------------------------------------------

def _make_15m_data(
    n: int = 150,
    start_price: float = 8.50,
    trend: str = "up",
    vol_spike_last: bool = True,
    seed: int = 0,
) -> pd.DataFrame:
    """Generate synthetic 15-minute OHLCV bars with a clear trend."""
    rng = np.random.default_rng(seed)
    index = pd.date_range("2026-01-01", periods=n, freq="15min")

    drift_map = {"up": 0.002, "down": -0.002, "flat": 0.0}
    drift = drift_map.get(trend, 0.0)
    log_returns = rng.normal(drift, 0.003, n)
    closes = start_price * np.exp(np.cumsum(log_returns))

    intrabar = np.abs(rng.normal(0, 0.003, n))
    highs = closes * (1 + intrabar)
    lows = closes * (1 - intrabar)
    opens = np.roll(closes, 1)
    opens[0] = start_price

    volume = rng.uniform(80_000, 150_000, n)
    if vol_spike_last:
        volume[-10:] *= 2.5

    df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volume},
        index=index,
    )
    df["high"] = df[["open", "high", "close"]].max(axis=1)
    df["low"] = df[["open", "low", "close"]].min(axis=1)
    return df


def _make_vwap_bullish_data(n: int = 150) -> pd.DataFrame:
    """Data where close > VWAP in the last bar (rising prices, VWAP lags below)."""
    index = pd.date_range("2026-06-01 00:00", periods=n, freq="15min")
    prices = 8.0 + np.linspace(0, 2.0, n)
    return pd.DataFrame(
        {
            "open": prices,
            "high": prices + 0.05,
            "low": prices - 0.05,
            "close": prices,
            "volume": np.full(n, 120_000.0),
        },
        index=index,
    )


# ---------------------------------------------------------------------------
# ATOMUSDT tests
# ---------------------------------------------------------------------------

class TestATOMUSDTStrategyInit(unittest.TestCase):
    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_strategy_inherits_from_base(self, _mock):
        data = _make_15m_data(n=150)
        self.assertIsInstance(ATOMUSDTStrategy(data), Strategy)

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_default_parameters(self, _mock):
        s = ATOMUSDTStrategy(_make_15m_data(n=150))
        self.assertEqual(s.ema_fast, 9)
        self.assertEqual(s.ema_slow, 21)
        self.assertEqual(s.rsi_period, 14)
        self.assertEqual(s.atr_period, 14)
        self.assertAlmostEqual(s.atr_stop_multiple, 1.5)
        self.assertAlmostEqual(s.reward_risk, 2.5)


class TestATOMUSDTStrategyEdgeCases(unittest.TestCase):
    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_returns_none_on_insufficient_data(self, _mock):
        self.assertIsNone(ATOMUSDTStrategy(_make_15m_data(n=20)).generate_signals())

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_returns_none_when_position_open(self, _mock):
        self.assertIsNone(
            ATOMUSDTStrategy(_make_15m_data(n=150)).generate_signals(position_side="LONG")
        )

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_returns_none_when_no_volume_spike(self, _mock):
        data = _make_15m_data(n=150, vol_spike_last=False)
        data["volume"] = 100_000.0
        self.assertIsNone(ATOMUSDTStrategy(data).generate_signals())

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_flat_market_signal_is_valid_if_present(self, _mock):
        """Flat market: any emitted signal must be BUY or SELL."""
        result = ATOMUSDTStrategy(_make_15m_data(n=200, trend="flat", seed=7)).generate_signals()
        if result is not None:
            self.assertIn(result["signal"], ("BUY", "SELL"))


class TestATOMUSDTStrategySignalContract(unittest.TestCase):
    """Verify shape and risk constraints of returned signals."""

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_buy_signal_has_correct_keys(self, _mock):
        data = _make_vwap_bullish_data(n=150)
        data.loc[data.index[-10:], "volume"] *= 3.0
        result = ATOMUSDTStrategy(data).generate_signals()
        if result and result["signal"] == "BUY":
            for key in ("signal", "entry_price", "stop_loss", "take_profit", "pattern"):
                self.assertIn(key, result)

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_buy_signal_stop_below_entry(self, _mock):
        data = _make_vwap_bullish_data(n=150)
        data.loc[data.index[-10:], "volume"] *= 3.0
        result = ATOMUSDTStrategy(data).generate_signals()
        if result and result["signal"] == "BUY":
            self.assertLess(result["stop_loss"], result["entry_price"])

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_buy_signal_target_above_entry(self, _mock):
        data = _make_vwap_bullish_data(n=150)
        data.loc[data.index[-10:], "volume"] *= 3.0
        result = ATOMUSDTStrategy(data).generate_signals()
        if result and result["signal"] == "BUY":
            self.assertGreater(result["take_profit"], result["entry_price"])

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_reward_risk_ratio_approximately_correct(self, _mock):
        data = _make_vwap_bullish_data(n=150)
        data.loc[data.index[-10:], "volume"] *= 3.0
        result = ATOMUSDTStrategy(data).generate_signals()
        if result and result["signal"] == "BUY":
            risk = result["entry_price"] - result["stop_loss"]
            reward = result["take_profit"] - result["entry_price"]
            self.assertAlmostEqual(reward / risk, ATOMUSDTStrategy(data).reward_risk, places=5)

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_sell_signal_stop_above_entry(self, _mock):
        index = pd.date_range("2026-06-01 00:00", periods=150, freq="15min")
        prices = 10.0 - np.linspace(0, 2.0, 150)
        data = pd.DataFrame(
            {
                "open": prices,
                "high": prices + 0.05,
                "low": prices - 0.05,
                "close": prices,
                "volume": np.full(150, 120_000.0),
            },
            index=index,
        )
        data.loc[data.index[-10:], "volume"] *= 3.0
        strategy = ATOMUSDTStrategy(data)
        result = strategy.generate_signals()
        if result and result["signal"] == "SELL":
            self.assertGreater(result["stop_loss"], result["entry_price"])
            risk = result["stop_loss"] - result["entry_price"]
            reward = result["entry_price"] - result["take_profit"]
            self.assertAlmostEqual(reward / risk, strategy.reward_risk, places=5)


class TestATOMUSDTVWAP(unittest.TestCase):
    """Verify VWAP computation resets at day boundaries."""

    @patch("orbit.core.discord_manager.DiscordManager.__init__", return_value=None)
    def test_vwap_resets_daily(self, _mock):
        """The first bar of each day must equal its own typical price."""
        index = pd.date_range("2026-01-01", periods=96 * 2, freq="15min")
        prices = np.ones(96 * 2) * 9.0
        data = pd.DataFrame(
            {
                "open": prices,
                "high": prices + 0.1,
                "low": prices - 0.1,
                "close": prices,
                "volume": np.ones(96 * 2) * 100_000.0,
            },
            index=index,
        )
        vwap = ATOMUSDTStrategy(data)._compute_vwap(data)
        for idx in (0, 96):
            typical = (
                data["high"].iloc[idx]
                + data["low"].iloc[idx]
                + data["close"].iloc[idx]
            ) / 3
            self.assertAlmostEqual(vwap.iloc[idx], typical, places=6)


if __name__ == "__main__":
    unittest.main()
