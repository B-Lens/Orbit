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
    