import os
import numpy as np
import pandas as pd
from dataclasses import dataclass
from abc import ABC, abstractmethod
from orbit.core.discord_manager import DiscordManager

WHEREIAM = os.environ.get('WHEREIAM')

@dataclass
class Strategy(DiscordManager, ABC):
    """
    Base class for implementing trading strategies.
    Custom strategies should inherit from this class and override
    the `generate_signals` method.
    """

    data: pd.DataFrame

    @abstractmethod
    def generate_signals(self) -> None:
        """
        Generate trading signals.
        Must be implemented by concrete strategies.
        """
        pass

    def send_params(self, stock_df, symbol=None, duration="", **kwargs):
        columns_found = stock_df.columns

        params = {}
        for column in columns_found:
            value = stock_df[column].iloc[-1]
            if isinstance(value, float):
                params[f"{column}"] = f"{stock_df[column].iloc[-1]:.2f}"
            else:
                params[f"{column}"] = f"{stock_df[column].iloc[-1]}"

        params.update(kwargs)

        self.send_parameters(
            data=None,
            description=f"{symbol} {duration} Candlestick update",
            fields=params,
        )

    @staticmethod
    def calculate_atr(data: pd.DataFrame, period: int = 14) -> pd.Series:
        """Return a rolling average true range used by production strategies."""
        previous_close = data["close"].shift(1)
        true_range = pd.concat(
            [
                data["high"] - data["low"],
                (data["high"] - previous_close).abs(),
                (data["low"] - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        return true_range.rolling(window=period, min_periods=1).mean()

    @staticmethod
    def compute_ema(series: pd.Series, period: int = 200) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def compute_atr(data: pd.DataFrame, period: int = 14) -> pd.Series:
        previous_close = data["close"].shift(1)
        true_range = pd.concat(
            [
                data["high"] - data["low"],
                (data["high"] - previous_close).abs(),
                (data["low"] - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        return true_range.rolling(window=period).mean()
