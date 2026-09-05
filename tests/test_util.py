import os
import pandas as pd
import unittest
import datetime
from pathlib import Path
from orbit.utils.utils import get_indian_time, generate_chart


class TestUtil(unittest.TestCase):
    def test_chart_dependencies_are_installed_with_runtime(self):
        pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
        pyproject = pyproject_path.read_text(encoding="utf-8")
        runtime_dependencies = pyproject.split(
            "[tool.poetry.dependencies]", maxsplit=1
        )[1].split("[", maxsplit=1)[0]

        self.assertIn('matplotlib = "*"', runtime_dependencies)
        self.assertIn('mplfinance = "*"', runtime_dependencies)

    def test_indian_time(self):
        indian_time = get_indian_time()

        self.assertIsInstance(indian_time, datetime.datetime)
        self.assertEqual(
            indian_time.utcoffset(), datetime.timedelta(hours=5, minutes=30)
        )

    def test_generate_chart_creates_file(self):
        # Create dummy OHLCV DataFrame
        data = {
            "open": [100, 102, 104, 103, 105],
            "high": [103, 105, 106, 107, 108],
            "low": [99, 100, 102, 101, 103],
            "close": [102, 104, 105, 106, 107],
            "volume": [1000, 1500, 1200, 1300, 1400],
        }
        index = pd.date_range(start="2024-01-01", periods=5, freq="15min")
        df = pd.DataFrame(data, index=index)

        # Sample support/resistance levels
        support = 101
        resistance = 107

        # Run the chart generator
        chart_path = generate_chart(df, support, resistance)

        # Assert file is created and exists
        self.assertTrue(os.path.exists(chart_path), "Chart image file was not created.")

        # Clean up generated chart file
        os.remove(chart_path)

        # Assert file is deleted properly
        self.assertFalse(
            os.path.exists(chart_path), "Chart image file was not removed after test."
        )


if __name__ == "__main__":
    unittest.main(exit=True)
