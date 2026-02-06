import os
from orbit_strategies.swing_strategy import *
from orbit_strategies.reversal_strategy import *
from orbit_strategies.Agglo_strategy import *
from orbit_strategies.strategies_base import OCSMA_CrossOver, MovingAverageCrossoverStrategy

WHEREIAM = os.environ.get('WHEREIAM')
STRATEGY_REGISTRY = {
    'BNBUSDT': ReversalStrategyBNB,
    'MKRUSDT': MeanReversionBBRSIStrategyMKR,
    'BCHUSDT': BollingerAdaptiveReversalStrategyBCH,
    'SOLUSDT': MeanReversionBBRSIStrategySOL,
    'LTCUSDT': ReversalStrategyLTC,
    'ETHUSDT': Agglo_ETHERIUM,
    'BTCUSDT': SwingStrategyBTC,
    'ocsma_crossover': OCSMA_CrossOver,
    'moving_crossover': MovingAverageCrossoverStrategy,
}