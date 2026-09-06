"""
signal_analyzer
===============

Provides :class:`SignalAnalyzer`, which iterates over configured trading
pairs, runs each symbol's registered strategy, and yields actionable
trading signals.

Dependencies (:class:`MongoHandler`, Redis) can be **injected** through
the constructor for easier testing and looser coupling.
"""

import time
import logging
import threading
import uuid
from importlib import metadata
from typing import Any, Dict, Iterator, Mapping, Optional

import redis

from orbit.core.authentication_manager import AuthenticationManager
from orbit.core.mongo_handler import MongoHandler
from orbit.core.redis_manager import RedisManager
from orbit.core.command_center import record_runtime_activity
from orbit.strategies.strategy_registry import STRATEGY_REGISTRY
from orbit.utils.utils import get_indian_time

logger = logging.getLogger("Orbit")


class SignalAnalyzer(AuthenticationManager, RedisManager):
    """Market signal generator.

    For every configured trading pair the analyser:

    1. Fetches / updates historical OHLCV data via :class:`MongoHandler`.
    2. Instantiates the symbol's registered strategy.
    3. Generates and validates signals (cooldown, sentiment, breakout filter).
    4. Yields actionable signal dicts to the caller.

    Args:
        mongo_handler: Pre-built :class:`MongoHandler`.  A new instance is
            created when ``None``.
        redis_client: Pre-built ``redis.StrictRedis`` connection.  A default
            ``localhost:6379/0`` connection is created when ``None``.
        **auth_kwargs: Forwarded to :class:`AuthenticationManager`.
    """

    def __init__(
        self,
        mongo_handler: Optional[MongoHandler] = None,
        redis_client: Optional[redis.StrictRedis] = None,
        **auth_kwargs: Any,
    ) -> None:
        AuthenticationManager.__init__(self, **auth_kwargs)
        RedisManager.__init__(self, redis_client=redis_client)

        if mongo_handler is not None:
            self.mongo_handler: MongoHandler = mongo_handler
        else:
            try:
                self.mongo_handler = MongoHandler()
            except Exception as e:
                self.handle_exception(e, "Exception while Creating MongoHandler")

    @staticmethod
    def _strategy_identity(strategy_class: type) -> Dict[str, str]:
        try:
            package_version = metadata.version("orbit")
        except metadata.PackageNotFoundError:
            package_version = "development"
        return {
            "strategy": f"{strategy_class.__module__}.{strategy_class.__name__}",
            "strategy_version": package_version,
        }

    def _record_decision(self, **values: Any) -> str:
        decision_id = str(values.pop("decision_id", uuid.uuid4()))
        symbol = str(values.get("symbol", ""))
        record = {
            "decision_id": decision_id,
            "timestamp": get_indian_time(),
            "execution_mode": self.execution_settings.mode_for(symbol).value,
            **values,
        }
        if getattr(self, "mongo_handler", None) is not None:
            self.mongo_handler.store_trade_decision(record)
        else:
            logger.warning(
                "Trade decision %s was not persisted: MongoDB unavailable", decision_id
            )
        return decision_id

    def analyze_market(
        self, unavailable_symbols: Mapping[str, Mapping[str, Any]]
    ) -> Iterator[Dict[str, Any]]:
        """Iterate over trading pairs and yield actionable signal dicts.

        Args:
            unavailable_symbols: Entry-blocked symbols and their current state.

        Yields:
            Signal dictionaries ready for order processing.
        """
        try:
            for symbol in self.trading_pairs:

                record_runtime_activity(
                    self.redis_client,
                    "analyzing_signal",
                    f"Running strategy for {symbol}",
                )

                self.send_logs(
                    data=None, description=f"Analyzing market for {symbol}", fields=None
                )

                strategy_class = STRATEGY_REGISTRY.get(symbol)
                if not strategy_class:
                    self.send_alerts(f"No strategy found for {symbol}", None)
                    self._record_decision(
                        symbol=symbol, outcome="rejected", reason="strategy_not_found"
                    )
                    continue

                strategy_identity = self._strategy_identity(strategy_class)
                availability = unavailable_symbols.get(symbol)
                if availability is not None:
                    reason = str(availability.get("reason", "post_exit_cooldown"))
                    self.send_cooldown_update(
                        data=None,
                        description=f"{symbol} entry blocked: {reason}",
                        fields=dict(availability),
                    )
                    # Availability states are not candidate trades. They are
                    # deliberately excluded from the blocked-trade audit ledger.
                    continue

                execution_mode = self.execution_settings.mode_for(symbol).value
                historical_data = self.mongo_handler.handle_mongo_data(
                    symbol, execution_mode=execution_mode
                )
                if historical_data.empty:
                    self.send_alerts(f"No historical data found for {symbol}", None)
                    self._record_decision(
                        symbol=symbol,
                        outcome="rejected",
                        reason="historical_data_unavailable",
                    )
                    continue

                try:
                    strategy = strategy_class(historical_data)
                    try:
                        params_thread = threading.Thread(
                            target=strategy.send_params,
                            kwargs={
                                "stock_df": historical_data,
                                "symbol": symbol,
                                "duration": "15 MIN",
                            },
                            daemon=True,
                            name=f"OHLCVParams-{symbol}",
                        )
                        params_thread.start()
                    except Exception as exc:
                        logger.warning(
                            "Unable to publish OHLCV params for %s: %s", symbol, exc
                        )
                    signal_ss = time.perf_counter()
                    signal_dict = strategy.generate_signals(symbol=symbol)
                    signal_es = time.perf_counter()
                except Exception as e:
                    self.handle_exception(
                        e, f"Exception while generating signals for {symbol}"
                    )
                    self._record_decision(
                        symbol=symbol,
                        outcome="error",
                        reason="strategy_exception",
                        **strategy_identity,
                    )
                    continue

                if not signal_dict:
                    self.send_signal_updates(
                        data=None, description=f"{symbol}: No Signal Found", fields=None
                    )
                    self._record_decision(
                        symbol=symbol,
                        outcome="no_signal",
                        reason="strategy_no_signal",
                        **strategy_identity,
                    )
                    time.sleep(0.5)
                    continue

                signal = signal_dict.get("signal")
                chart_path_raw = signal_dict.get("chart_path_raw")
                pattern = signal_dict.get("pattern") or "unknown"

                sentiment = self.get_market_sentiment()
                if self._should_skip_due_to_sentiment(
                    signal, symbol, signal_dict, sentiment=sentiment
                ):
                    self._record_decision(
                        symbol=symbol,
                        signal=signal,
                        pattern=pattern,
                        sentiment=sentiment,
                        entry_price=signal_dict.get("entry_price"),
                        stop_loss=signal_dict.get("stop_loss"),
                        take_profit=signal_dict.get("take_profit"),
                        outcome="rejected",
                        reason="sentiment_conflict",
                        **strategy_identity,
                    )
                    continue

                try:
                    options = {"signal": signal, "pattern": pattern}

                    if chart_path_raw:
                        self.send_chart_to_webhook(
                            file_path=chart_path_raw,
                            data=None,
                            description=f"{symbol}, signal = {signal}",
                            fields=options,
                        )

                    decision_id = self._record_decision(
                        symbol=symbol,
                        signal=signal,
                        pattern=pattern,
                        sentiment=sentiment,
                        entry_price=signal_dict.get("entry_price"),
                        stop_loss=signal_dict.get("stop_loss"),
                        take_profit=signal_dict.get("take_profit"),
                        outcome="accepted",
                        reason="passed_filters",
                        **strategy_identity,
                    )
                    signal = {
                        "decision_id": decision_id,
                        "symbol": symbol,
                        "signal": signal,
                        "timestamp": get_indian_time(),
                        "entry_price": signal_dict.get("entry_price"),
                        "stop_loss": signal_dict.get("stop_loss"),
                        "take_profit": signal_dict.get("take_profit"),
                        "pattern": pattern,
                        "sentiment": sentiment,
                        **strategy_identity,
                        "Other Info": f"Time execution for signal Analysis = {signal_es - signal_ss:.4f} seconds",
                    }
                    yield signal
                except Exception as e:
                    self.handle_exception(e, f"Exception preparing signal for {symbol}")

                time.sleep(0.5)

        except Exception as e:
            self.handle_exception(e, "Exception in analyze_market")

    def _should_skip_due_to_sentiment(
        self,
        signal: str,
        symbol: str,
        trade_info: Dict[str, Any],
        sentiment: Optional[str] = None,
    ) -> bool:
        """Check Redis for market sentiment and decide whether to skip.

        A signal is skipped when it contradicts the prevailing sentiment.

        Returns:
            ``True`` if the signal should be discarded.
        """
        try:
            sentiment = (
                sentiment if sentiment is not None else self.get_market_sentiment()
            )
            if sentiment:
                skip = False

                if sentiment == "BULLISH" and signal == "SELL":
                    self.send_alerts(
                        data=f"{symbol}",
                        description=f"Positive sentiment, but Sell signal",
                        fields=None,
                    )
                    skip = True
                elif sentiment == "BEARISH" and signal == "BUY":
                    self.send_alerts(
                        data=f"{symbol}",
                        description=f"Negative sentiment, but Buy signal",
                        fields=None,
                    )
                    skip = True

                if skip:
                    return True

        except Exception as e:
            self.handle_exception(e, f"Sentiment check failed for {symbol}")
        return False
