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
