"""
orbit.core
==========

Core module for the Binance trading automation system.

Public classes:
    - DiscordManager: Discord webhook notification management.
    - ExceptionManager: Centralised exception handling and reporting.
    - AuthenticationManager: Binance API authentication and client setup.
    - MongoHandler: MongoDB OHLCV data storage and retrieval.
    - OrderManager: Binance Futures order lifecycle management.
    - TradeChecker: Active-position monitoring and SL/TP maintenance.
    - SignalAnalyzer: Market signal generation from strategies.
    - Croner: Scheduled sentiment-analysis cron runner.
    - BinanceAutomation: Top-level automation controller.
"""

from orbit.core.discord_manager import DiscordManager
from orbit.core.exception_manager import ExceptionManager
from orbit.core.authentication_manager import AuthenticationManager
from orbit.core.mongo_handler import MongoHandler
from orbit.core.order_manager import OrderManager
from orbit.core.trade_checker import TradeChecker
from orbit.core.signal_analyzer import SignalAnalyzer
from orbit.core.sentimen_cron import Croner
from orbit.core.main import BinanceAutomation

__all__ = [
    "DiscordManager",
    "ExceptionManager",
    "AuthenticationManager",
    "MongoHandler",
    "OrderManager",
    "TradeChecker",
    "SignalAnalyzer",
    "Croner",
    "BinanceAutomation",
]