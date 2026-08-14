from enum import Enum
import logging
import logging.config
import yaml

class TradeType(Enum):
    BRACKET_TRADE = "bracket_trade"
    ADAPTIVE_TRADE = "adaptive_trade"

COIN_TRADE_TYPE = {
    "BNBUSDT": TradeType.BRACKET_TRADE,
    "MKRUSDT": TradeType.BRACKET_TRADE,
    "BCHUSDT": TradeType.ADAPTIVE_TRADE,
    "SOLUSDT": TradeType.BRACKET_TRADE,
    "LTCUSDT": TradeType.BRACKET_TRADE,
    "ETHUSDT": TradeType.BRACKET_TRADE,
    "BTCUSDT": TradeType.BRACKET_TRADE,
}

TRAILING_STOPLOSS = {
    "BTCUSDT": False,
    "ETHUSDT": False,
    "BCHUSDT": False,
    "BNBUSDT": False,
    "MKRUSDT": False,
    "LTCUSDT": False,
    "SOLUSDT": False,
    "ATOMUSDT": False,
    "XRPUSDT": False
}

# Load YAML config
with open("config/logging_config.yaml", "r") as f:
    config = yaml.safe_load(f.read())
    logging.config.dictConfig(config)

# Get your logger
logger = logging.getLogger("Orbit")

# Example logs
logger.debug("Debug message (only file)")
logger.info("Info message (console + file)")
logger.warning("Warning message with filename")