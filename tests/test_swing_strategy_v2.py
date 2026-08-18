"""Unit tests for SwingStrategyBTC_V2 indicators and signal logic."""

import unittest
import pandas as pd
import numpy as np

from orbit.strategies.swing_strategy_v2 import SwingStrategyBTC_V2


class TestSwingV2Indicators(unittest.TestCase):
    """Test the static indicator methods of SwingStrategyBTC_V2."""

    def _make_price_series(self, n=100, base=100, seed=42):
        np.random.seed(seed)
        returns = np.random.normal(0, 0.01, n)
        prices = base * np.cumprod(1 + returns)
        return pd.Series(prices)

    def _make_ohlcv_df(self, n=200, base=100, seed=42):
        np.random.seed(seed)
        dates = pd.date_range("2026-01-01", periods=n, freq="4h")
        closes = base * np.cumprod(1 + np.random.normal(0, 0.01, n))
        highs = closes * (1 + np.random.uniform(0, 0.02, n))
        lows = closes * (1 - np.random.uniform(0, 0.02, n))
        opens = closes * (1 + np.random.normal(0, 0.005, n))
        volumes = np.random.uniform(100, 1000, n)
        return pd.DataFrame({
            "open": opens, "high": highs, "low": lows,
            "close": closes, "volume": volumes,
        }, index=dates)

    def test_rsi_bounds(self):
        """RSI should always be in [0, 100]."""
        series = self._make_price_series(200)
        rsi = SwingStrategyBTC_V2.compute_rsi(series, period=14)
        self.assertTrue((rsi.dropna() >= 0).all())
        self.assertTrue((rsi.dropna() <= 100).all())

    def test_rsi_constant_prices(self):
        """RSI for constant prices should be 50 (no gains, no losses)."""
        series = pd.Series([100.0] * 50)
        rsi = SwingStrategyBTC_V2.compute_rsi(series, period=14)
        # With constant prices, gains and losses are both 0, RSI approaches 50
        # Due to EWM initialization, check last values
        last_rsi = rsi.iloc[-1]
        # With zero changes, RSI can be NaN (0/0) or 50
        self.assertTrue(pd.isna(last_rsi) or abs(last_rsi - 50) < 1)

    def test_rsi_rising_prices(self):
        """RSI for steadily rising prices should be above 50."""
        series = pd.Series(range(100, 150))
        rsi = SwingStrategyBTC_V2.compute_rsi(pd.Series(series, dtype=float), period=14)
        self.assertGreater(rsi.iloc[-1], 50)

    def test_adx_output_shape(self):
        """ADX output should have the same length as input DataFrame."""
        df = self._make_ohlcv_df(100)
        adx = SwingStrategyBTC_V2.compute_adx(df, period=14)
        self.assertEqual(len(adx), len(df))

    def test_adx_non_negative(self):
        """ADX values should be non-negative."""
        df = self._make_ohlcv_df(200)
        adx = SwingStrategyBTC_V2.compute_adx(df, period=14)
        self.assertTrue((adx.dropna() >= 0).all())

    def test_atr_mult_is_1_5(self):
        """V2 strategy should use atr_mult=1.5 (not 1.0 like V1)."""
        df = self._make_ohlcv_df(50)
        # We can't instantiate without mocking, so just check the class default
        self.assertEqual(SwingStrategyBTC_V2.atr_mult, 1.5)

    def test_tp_rr_is_2(self):
        """Take profit risk-reward should be 2."""
        self.assertEqual(SwingStrategyBTC_V2.tp_rr, 2.0)

    def test_pivot_high_detection(self):
        """Pivot high detection should find the center of a symmetric window."""
        # Create a series with a clear peak at the center
        series = [10, 11, 12, 13, 14, 15, 14, 13, 12, 11, 10]
        # n=5, left=5, right=5 → window = 11 elements, center at index 5 = 15
        df = self._make_ohlcv_df(50)
        # Mock strategy just for pivot method access
        result = SwingStrategyBTC_V2.pivot_high_centered(None, series, 5, 5)
        self.assertEqual(result, 15)

    def test_pivot_high_no_detection(self):
        """Pivot high should return None if center is not the max."""
        series = [10, 11, 12, 13, 14, 13, 16, 13, 12, 11, 10]
        result = SwingStrategyBTC_V2.pivot_high_centered(None, series, 5, 5)
        self.assertIsNone(result)


class TestSwingV2Parameters(unittest.TestCase):
    """Test the parameter defaults of SwingStrategyBTC_V2."""

    def test_default_sl_limit(self):
        self.assertEqual(SwingStrategyBTC_V2.max_sl_limit, 2.0)

    def test_default_atr_mult(self):
        """V2 uses 1.5x ATR (improved from V1's 1.0x)."""
        self.assertEqual(SwingStrategyBTC_V2.atr_mult, 1.5)

    def test_default_n(self):
        self.assertEqual(SwingStrategyBTC_V2.n, 10)


if __name__ == "__main__":
    unittest.main()
