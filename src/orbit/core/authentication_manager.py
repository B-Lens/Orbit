import os
import locale
from binance.spot import Spot
from binance.um_futures import UMFutures
from dataclasses import dataclass

import logging
from config.config import *
from orbit.core.exception_manager import ExceptionManager

logger = logging.getLogger("Orbit")


@dataclass
class Authenticator(ExceptionManager):
    def __init__(self):
        self.config_json = load_config()
        lang, encoding = locale.getdefaultlocale()

        BINANCE_API_KEY = os.getenv("BINANE_API_KEY")
        SECRET_KEY = os.getenv("SECRET_KEY")

        if lang == "en_US":
            self.client = Spot(
                BINANCE_API_KEY,
                SECRET_KEY,
                base_url="https://api.binance.us",
            )
            logger.info(f"https://api.binance.us :Authenticated")
            
        else:
            self.client = Spot(
                BINANCE_API_KEY,
                SECRET_KEY,
            )
        self.future_client = UMFutures(
            BINANCE_API_KEY,
            secret=SECRET_KEY,
        )

        self.trading_pairs = self.config_json["trading_pairs"]
        self.trade_checker_pair = self.config_json["trade_checker_pair"]

    def message_handler(self, _, message) -> None:
        logger.info(message)

    def get_spot_symbol_price(self, symbol) -> float:
        ticker = self.client.ticker_price(symbol=symbol)
        current_price = float(ticker["price"])
        return current_price

    def get_future_symbol_price(self, symbol) -> float:
        ticker = self.future_client.ticker_price(symbol=symbol)
        current_price = float(ticker["price"])
        return current_price
