import unittest
from unittest.mock import patch

import pandas as pd

from orbit.strategies.btc_strategy import BTCStrategy


def hourly_frame(closes: list[float]) -> pd.DataFrame:
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
        data = hourly_frame([100.0] * 55 + [105.0])
        signal = BTCStrategy(data).generate_signals(symbol="BTCUSDT")

        self.assertEqual(signal["signal"], "BUY")
        risk = signal["entry_price"] - signal["stop_loss"]
        reward = signal["take_profit"] - signal["entry_price"]
        self.assertAlmostEqual(reward / risk, 3.0)

    @patch("orbit.strategies.btc_strategy.generate_chart", return_value=None)
    def test_short_breakout_has_three_to_one_reward_risk(self, _chart):
        data = hourly_frame([100.0] * 55 + [95.0])
        signal = BTCStrategy(data).generate_signals(symbol="BTCUSDT")

        self.assertEqual(signal["signal"], "SELL")
        risk = signal["stop_loss"] - signal["entry_price"]
        reward = signal["entry_price"] - signal["take_profit"]
        self.assertAlmostEqual(reward / risk, 3.0)

    def test_partial_hour_does_not_emit_signal(self):
        hourly = hourly_frame([100.0] * 55 + [105.0])
        rows = []
        for timestamp, row in hourly.iterrows():
            for offset in range(4):
                item = row.copy()
                rows.append((timestamp + pd.Timedelta(minutes=15 * offset), item))
        data = pd.DataFrame([row for _, row in rows], index=[time for time, _ in rows])
        data = data.iloc[:-1]

        self.assertIsNone(BTCStrategy(data).generate_signals(symbol="BTCUSDT"))

    def test_trailing_stop_uses_recent_price_structure(self):
        data = hourly_frame([100.0] * 55 + [105.0])
        signal = BTCStrategy(data).generate_signals(
            symbol="BTCUSDT", position_side="LONG"
        )

        self.assertEqual(signal["signal"], "UPDATE_SL_TP")
        self.assertLess(signal["stop_loss"], data["high"].iloc[-12:].max())


if __name__ == "__main__":
    unittest.main()
