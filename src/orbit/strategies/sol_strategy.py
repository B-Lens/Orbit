"""Paper-first Solana strategy candidates.

SOL is intentionally kept separate from production strategies so it can be
forward-tested without implying that the parameters have been optimized or
approved for live capital.
"""

import pandas as pd

from orbit.strategies.reversal_strategy import BollingerAdaptiveReversalStrategyBCH


class BollingerAdaptiveReversalStrategySOL(BollingerAdaptiveReversalStrategyBCH):
    """Initial SOLUSDT paper-trading candidate.

    The first candidate deliberately reuses Orbit's existing Bollinger reversal
    rules and parameters.  This gives the paper test a known baseline while the
    SOL-specific evidence is collected.  Parameter tuning should happen only
    after enough out-of-sample paper trades have accumulated.
    """

    def __init__(
        self,
        data: pd.DataFrame,
        bb_period: int = 20,
        bb_devfactor: float = 3.0,
        sma_period: int = 20,
        sl_pct: float = 0.015,
    ) -> None:
        super().__init__(
            data=data,
            bb_period=bb_period,
            bb_devfactor=bb_devfactor,
            sma_period=sma_period,
            sl_pct=sl_pct,
        )
