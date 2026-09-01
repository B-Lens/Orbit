import unittest

import pandas as pd

from orbit.strategies.mkrusdt_strategy import MKRUSDTStrategy


def hourly_frame(final_close: float) -> pd.DataFrame:
    closes = [100.0] * 105 + [final_close]
    return pd.DataFrame(
        {
            "open": closes,
            "high": [value + 1 for value in closes],
            "low": [value - 1 for value in closes],
            "close": closes,
            "volume": [100.0] * len(closes),
        },
        index=pd.date_range("2025-01-01", periods=len(closes), freq="1h"),
    )


class TestMKRUSDTStrategy(unittest.TestCase):
    def test_long_breakout_has_four_to_one_reward_risk(self):
        signal = MKRUSDTStrategy(hourly_frame(105.0)).generate_signals()

        self.assertEqual(signal["signal"], "BUY")
        risk = signal["entry_price"] - signal["stop_loss"]
        reward = signal["take_profit"] - signal["entry_price"]
        self.assertAlmostEqual(reward / risk, 4.0)

    def test_short_breakout_has_four_to_one_reward_risk(self):
        signal = MKRUSDTStrategy(hourly_frame(95.0)).generate_signals()

        self.assertEqual(signal["signal"], "SELL")
        risk = signal["stop_loss"] - signal["entry_price"]
        reward = signal["entry_price"] - signal["take_profit"]
        self.assertAlmostEqual(reward / risk, 4.0)

    def test_existing_position_suppresses_entry(self):
        signal = MKRUSDTStrategy(hourly_frame(105.0)).generate_signals(
            position_side="LONG"
        )

        self.assertIsNone(signal)

    def test_incomplete_resampled_hour_suppresses_entry(self):
        hourly = hourly_frame(105.0)
        rows = [
            (timestamp + pd.Timedelta(minutes=15 * offset), row)
            for timestamp, row in hourly.iterrows()
            for offset in range(4)
        ]
        partial = pd.DataFrame(
            [row for _, row in rows], index=[timestamp for timestamp, _ in rows]
        ).iloc[:-1]

        self.assertIsNone(MKRUSDTStrategy(partial).generate_signals())


if __name__ == "__main__":
    unittest.main()
