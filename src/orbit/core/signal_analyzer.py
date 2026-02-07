import time
import redis
import logging
from typing import List, Dict, Iterator

from orbit.core.authentication_manager import Authenticator
from orbit.utils.utils import get_indian_time

from orbit.core.mongo_handler import MongoHandler
from orbit.strategies.strategy_registry import STRATEGY_REGISTRY

# Initialize logging
logger = logging.getLogger("Orbit")


class SignalAnalyzer(Authenticator):
    def __init__(self):
        super().__init__()
        self.redis_client = redis.StrictRedis(host="localhost", port=6379, db=0, decode_responses=True)

        try:
            self.mongo_handler = MongoHandler()
        except Exception as e:
            self.handle_exception(e, "Exception while Creating MongoHandler")

    def analyze_market(self, cooldown_symbols: List[str]) -> Iterator[Dict]:
        """
        Analyze the market for each trading pair, generate signals, and handle cooldowns and sentiment checks.
        Returns a list of signal dictionaries.
        """
        try:
            for symbol in self.trading_pairs:

                self.send_logs(data=None, description=f"Analyzing market for {symbol}", fields=None)

                historical_data = self.mongo_handler.handle_mongo_data(symbol)
                if historical_data.empty:
                    self.send_alerts(f"No historical data found for {symbol}", None)
                    continue

                strategy_class = STRATEGY_REGISTRY.get(symbol)
                if not strategy_class:
                    self.send_alerts(f"No strategy found for {symbol}", None)
                    continue

                self.strategy = strategy_class(historical_data)
                signal_ss = time.perf_counter()
                signal_dict = self.strategy.generate_signals(symbol=symbol)
                signal_es = time.perf_counter()

                if symbol in cooldown_symbols:
                    self.send_cooldown_update(data=None, description=f"{symbol} is in cooldown", fields=None)
                    continue

                if not signal_dict:
                    self.send_signal_updates(data=None, description=f"{symbol}: No Signal Found", fields=None)
                    time.sleep(0.5)
                    continue

                signal = signal_dict.get("signal")
                chart_path = signal_dict.get("chart_path")
                chart_path_raw = signal_dict.get("chart_path_raw")
                pattern = signal_dict.get("pattern")

                if 'breakout' in pattern.lower():
                    self.send_alerts(data=f"{symbol}", description=f"Pattern identified as Breakout: {pattern}", fields=None)
                    continue

                # Sentiment check
                if self._should_skip_due_to_sentiment(signal, symbol):
                    continue

                try:
                    options = {"signal": signal, "pattern": pattern}
                    optional = {"symbol": symbol}

                    if chart_path_raw:
                        self.send_chart_to_webhook(file_path=chart_path_raw, data=None, description=f"{symbol}, signal = {signal}", fields=options)

                    signal = {
                        "symbol": symbol,
                        "signal": signal,
                        "timestamp": get_indian_time(),
                        "entry_price": signal_dict.get("entry_price"),
                        "stop_loss": signal_dict.get("stop_loss"),
                        "take_profit": signal_dict.get("take_profit"),
                        "Other Info": f"Time execution for signal Analysis = {signal_es - signal_ss:.4f} seconds",
                    }
                    yield signal
                except Exception as e:
                    self.handle_exception(e, f"Exception at inference, SIGNAL = {signal[0]} for {symbol}")

                time.sleep(0.5)

        except Exception as e:
            self.handle_exception(e, "Exception in analyze_market")

    def _should_skip_due_to_sentiment(self, signal, symbol) -> bool:
        """
        Checks Redis for market sentiment and determines if the signal should be skipped.
        Returns True if the signal should be skipped, False otherwise.
        """
        try:
            sentiment = self.redis_client.get("market_sentiments")
            if sentiment:
                if sentiment == 'POSITIVE' and signal[0] == "SELL":
                    self.send_alerts(data=f"{symbol}", description=f"Positive sentiment, but Sell signal", fields=None)
                    return True
                elif sentiment == 'NEGATIVE' and signal[0] == "BUY":
                    self.send_alerts(data=f"{symbol}", description=f"Negative sentiment, but Buy signal", fields=None)
                    return True
        except Exception as e:
            self.handle_exception(e, f"Sentiment check failed for {symbol}")
        return False
