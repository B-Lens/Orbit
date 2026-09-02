from enum import Enum
import logging
import logging.config
import os
import yaml

class TradeType(Enum):
    BRACKET_TRADE = "bracket_trade"
    ADAPTIVE_TRADE = "adaptive_trade"

COIN_TRADE_TYPE = {
    "BNBUSDT": TradeType.BRACKET_TRADE,
    "MKRUSDT": TradeType.BRACKET_TRADE,
    "SKYUSDT": TradeType.BRACKET_TRADE,
    "BCHUSDT": TradeType.ADAPTIVE_TRADE,
    "SOLUSDT": TradeType.BRACKET_TRADE,
    "LTCUSDT": TradeType.BRACKET_TRADE,
    "ETHUSDT": TradeType.BRACKET_TRADE,
    "BTCUSDT": TradeType.BRACKET_TRADE,
    "PAXGUSDT": TradeType.BRACKET_TRADE,
}

TRAILING_STOPLOSS = {
    "BTCUSDT": False,
    "ETHUSDT": False,
    "BCHUSDT": False,
    "BNBUSDT": False,
    "MKRUSDT": False,
    "SKYUSDT": False,
    "LTCUSDT": False,
    "SOLUSDT": False,
    "ATOMUSDT": False,
    "XRPUSDT": False,
    "PAXGUSDT": False
}

# Load YAML config independently of the process working directory.
_CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_CONFIG_DIR, "logging_config.yaml"), "r", encoding="utf-8") as f:
    config = yaml.safe_load(f.read())
    config["handlers"]["file"]["filename"] = os.getenv("ORBIT_LOG_FILE", "app.log")
    logging.config.dictConfig(config)

# Get your logger
logger = logging.getLogger("Orbit")

# Example logs
logger.debug("Debug message (only file)")
logger.info("Info message (console + file)")
logger.warning("Warning message with filename")
