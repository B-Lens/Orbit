import pandas as pd

from orbit.strategies.sol_strategy import SolanaVolatilityMomentumStrategy


def _trend_frame(direction: int, rows: int = 30 * 24 * 4) -> pd.DataFrame:
    closes = [
        100.0
        + direction
        * ((i // 24) * 0.4 + (1.2 if (i // 24) % 4 else -1.2) + (i % 24) * 0.01)
        for i in range(rows)
    ]
    return pd.DataFrame(
        {
            "open": closes,
            "high": [value + 0.35 for value in closes],
            "low": [value - 0.35 for value in closes],
            "close": closes,
            "volume": [1000.0] * (rows - 1) + [1200.0],
        },
        index=pd.date_range("2026-07-01", periods=rows, freq="15min", tz="UTC"),
    )


def test_web_strategy_builds_atr_sized_long_plan():
    signal = SolanaVolatilityMomentumStrategy(_trend_frame(1)).generate_signals()

    assert signal is not None
    assert signal["signal"] == "BUY"
    assert signal["stop_loss"] < signal["entry_price"] < signal["take_profit"]
    assert signal["strategy_meta"]["source"] == "independent-web-research-2026-08-17"


def test_web_strategy_rejects_below_median_volume():
    data = _trend_frame(1)
    data.loc[data.index[-1], "volume"] = 1.0

    assert SolanaVolatilityMomentumStrategy(data).generate_signals() is None
