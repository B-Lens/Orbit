"""
main
====

Binance Cryptocurrency Trading Automation System (Thread-Based Version).

This module coordinates automated Binance Futures trading using a
multi-threaded architecture.  The system handles:

- Market signal generation
- Automated order placement
- Trade monitoring & cooldown logic
- Scheduled sentiment analysis (cron-like executor)
- Discord notifications
- Exception & error management

Threading is used because all operations are I/O-bound (network to
Binance), it has lower overhead than multiprocessing, Binance clients are
**not** pickle-safe, and it is much safer and simpler for long-running
trading systems.

Author: Pankaj Kumar
"""

import sys
import time
import logging
import threading
import traceback
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

from config.config import load_config
from orbit.core.signal_analyzer import SignalAnalyzer
from orbit.core.trade_checker import TradeChecker
from orbit.core.order_manager import OrderManager
from orbit.core.exception_manager import ExceptionManager
from orbit.core.sentimen_cron import Croner
from orbit.core.performance_reporter import PerformanceReporter
from orbit.core.testnet_reporter import TestnetDailyReporter
from orbit.core.execution import ExecutionMode
from orbit.core.trade_reasoner import TradeReasoner
from orbit.llm.llm_endpoint import LLM
from orbit.utils.utils import get_indian_time

# Constants
SIGNAL_ANALYSIS_SLEEP: int = 900  # 15 minutes in seconds

logger = logging.getLogger("Orbit")


def install_global_exception_handler(manager):
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            return

        traceback_str = "".join(
            traceback.format_exception(exc_type, exc_value, exc_traceback)
        )

        manager.exception_trigger(
            data=None,
            description=f"UNCAUGHT GLOBAL EXCEPTION\n{traceback_str}",
        )

    sys.excepthook = handle_exception

    def thread_exception_handler(args):
        handle_exception(args.exc_type, args.exc_value, args.exc_traceback)

    threading.excepthook = thread_exception_handler


class BinanceAutomation(ExceptionManager):
    """Top-level trading automation controller.

    Orchestrates three long-running daemon threads:

    1. **Signal analysis** — generates and processes trading signals.
    2. **Trade checker** — monitors active positions and manages SL/TP.
    3. **Sentiment cron** — runs sentiment analysis every 30 minutes.

    All major dependencies are accepted via the constructor so that the
    class can be tested or reconfigured without monkey-patching.

    Args:
        signal_analyzer: Pre-built :class:`SignalAnalyzer`.
        trade_checker: Pre-built :class:`TradeChecker`.
        order_manager: Pre-built :class:`OrderManager`.
        croner: Pre-built :class:`Croner` (sentiment scheduler).
        config: Application configuration dict.  Loaded from disk when ``None``.
    """

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def __init__(
        self,
        signal_analyzer: Optional[SignalAnalyzer] = None,
        trade_checker: Optional[TradeChecker] = None,
        order_manager: Optional[OrderManager] = None,
        croner: Optional[Croner] = None,
        testnet_reporter: Optional[TestnetDailyReporter] = None,
        trade_reasoner: Optional[TradeReasoner] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__()

        config_json: Dict[str, Any] = config if config is not None else load_config()

        # Core components — injected or created with defaults
        self.signal_analyzer: SignalAnalyzer = signal_analyzer or SignalAnalyzer()
        self.order_manager: OrderManager = order_manager or OrderManager()
        self.trade_checker: TradeChecker = trade_checker or TradeChecker(
            order_manager=self.order_manager
        )
        self._croner: Optional[Croner] = croner
        self._testnet_reporter = testnet_reporter
        self._trade_reasoner = trade_reasoner

        # Configuration
        self.trading_pairs: List[str] = config_json["trading_pairs"]
        self.trade_checker_pair: List[str] = config_json["trade_checker_pair"]
        self.risk_management: Dict[str, Any] = config_json["risk_management"]
        self.future_leverage: int = config_json["FUTURE_LEVERAGE"]

        # Active trade map (thread-local instance)
        self.trades: Dict[str, Any] = {}

        # Track all running worker threads for health monitoring
        self.workers_to_monitor: List[threading.Thread] = []

        logger.info("BinanceAutomation initialized")

    # ------------------------------------------------------------------
    # Order execution monitor (thread)
    # ------------------------------------------------------------------

    def monitor_order_execution(
        self,
        symbol: str,
        order_id: int,
        action: str,
        quantity: float,
        price: float,
        decision_id: Optional[str] = None,
    ) -> None:
        """Poll order status for up to 10 minutes; cancel on timeout.

        Designed to run in a dedicated daemon thread.

        Args:
            symbol: Trading pair.
            order_id: The ``orderId`` to monitor.
            action: ``"BUY"`` or ``"SELL"``.
            quantity: Order quantity.
            price: Limit price used for the order.
        """
        start_time = time.time()
        timeout_seconds = 600  # 10 minutes

        while time.time() - start_time < timeout_seconds:
            try:
                order = self.order_manager.get_order(symbol, order_id)
                order_list = [order] if order else []

                for order in order_list:
                    status = order.get("status")

                    if status in ("NEW",):
                        break

                    if status in ("CANCELED", "REJECTED", "EXPIRED"):
                        if decision_id and self.order_manager.mongo_handler is not None:
                            self.order_manager.mongo_handler.append_decision_event(
                                decision_id,
                                {
                                    "event_id": f"order_{status.lower()}:{symbol}:{order_id}",
                                    "status": f"order_{status.lower()}",
                                    "order_id": order_id,
                                },
                            )
                        self.send_alerts(
                            data=None,
                            description=f"Order {order_id} for {symbol} was {status.lower()}",
                            fields=order,
                        )
                        return

                    if status == "FILLED":
                        if decision_id and self.order_manager.mongo_handler is not None:
                            self.order_manager.mongo_handler.append_decision_event(
                                decision_id,
                                {
                                    "event_id": f"order_filled:{symbol}:{order_id}",
                                    "status": "order_filled",
                                    "order_id": order_id,
                                    "executed_quantity": order.get(
                                        "executedQty", quantity
                                    ),
                                    "average_price": order.get("avgPrice"),
                                },
                            )
                        self.trades[symbol] = {
                            "symbol": symbol,
                            "positionSide": action,
                            "quantity": quantity,
                            "orderId": order_id,
                            "price": price,
                            "trade_id": decision_id or symbol,
                            "entered_at": get_indian_time().isoformat(),
                        }
                        self.trade_checker.merge_trade_fields(
                            decision_id or symbol, self.trades[symbol]
                        )
                        self.send_signal_updates(
                            data=None,
                            description=f"Order {order_id} filled for {symbol}",
                            fields=order,
                        )
                        return

                time.sleep(30)

            except Exception as e:
                self.handle_exception(
                    e,
                    context_description=f"Exception monitoring order {order_id} for {symbol}",
                )
                time.sleep(30)

        # TIMEOUT → Cancel order
        try:
            cancel_result = self.order_manager.cancel_order(symbol, order_id)
            assert (
                cancel_result and cancel_result.get("status") == "CANCELED"
            ), "Cancellation failed"
            if decision_id and self.order_manager.mongo_handler is not None:
                self.order_manager.mongo_handler.append_decision_event(
                    decision_id,
                    {
                        "event_id": f"order_timeout_cancelled:{symbol}:{order_id}",
                        "status": "order_timeout_cancelled",
                        "order_id": order_id,
                    },
                )
            self.send_alerts(
                data=None,
                description=f"Order {order_id} for {symbol} cancelled (10 min timeout)",
                fields=cancel_result,
            )
        except Exception as e:
            self.handle_exception(
                e,
                context_description=f"Exception cancelling timed-out order {order_id} for {symbol}",
            )

    # ------------------------------------------------------------------
    # Signal processor
    # ------------------------------------------------------------------

    def process_signal(self, signal: Dict[str, Any]) -> None:
        """Translate a signal dict into a live order and start monitoring.

        Args:
            signal: Must contain ``symbol``, ``signal``, ``entry_price``,
                ``stop_loss``, ``take_profit``, and optionally ``Other Info``.
        """
        if not signal:
            return
        symbol = signal["symbol"]
        action = signal["signal"]
        entry_price = signal["entry_price"]
        stop_loss = signal["stop_loss"]
        target = signal["take_profit"]
        meta_info = signal.get("Other Info", "")
        decision_id = signal.get("decision_id")

        try:
            if self._trade_reasoner is None:
                self._trade_reasoner = TradeReasoner(LLM())
            entry_reasoning = self._trade_reasoner.review_entry(signal)
            if decision_id and self.order_manager.mongo_handler is not None:
                self.order_manager.mongo_handler.append_decision_event(
                    decision_id,
                    {
                        "status": (
                            "llm_entry_approved"
                            if entry_reasoning.take_trade
                            else "llm_entry_rejected"
                        ),
                        "llm_reasoning": TradeReasoner.serialize(entry_reasoning),
                    },
                )
            if not entry_reasoning.take_trade:
                self.send_alerts(
                    data=None,
                    description=f"LLM rejected trade for {symbol}",
                    fields=TradeReasoner.serialize(entry_reasoning),
                )
                return
        except Exception as error:
            logger.exception("Pre-trade LLM review failed for %s", symbol)
            if decision_id and self.order_manager.mongo_handler is not None:
                self.order_manager.mongo_handler.append_decision_event(
                    decision_id,
                    {
                        "status": "llm_entry_rejected",
                        "reason": "llm_review_failed",
                        "error": str(error),
                    },
                )
            self.send_alerts(
                data=None,
                description=f"Trade blocked because LLM review failed for {symbol}",
            )
            return

        if meta_info:
            self.send_logs(
                data=f"{symbol} - {action}",
                description=f"Signal Info: {meta_info}",
                fields=None,
            )

        price_to_use = entry_price or self.order_manager.get_symbol_price(symbol)
        leverage = 5 if symbol == "BTCUSDT" else self.future_leverage

        logger.info(f"Placing {action} order for {symbol}...")

        order_response, quantity, order_request = self.order_manager.place_order(
            self.risk_management,
            symbol,
            action,
            price_to_use,
            stop_loss,
            target,
            leverage,
            trade_id=decision_id,
        )

        time.sleep(0.5)

        if not order_response:
            self.send_alerts(data=None, description=f"Order failed for {symbol}")
            return

        order_id = order_response.get("orderId")
        monitor_thread = threading.Thread(
            target=self.monitor_order_execution,
            args=(
                symbol,
                order_id,
                action,
                quantity,
                order_request["price"],
                decision_id,
            ),
            daemon=True,
            name=f"OrderMonitor-{symbol}-{order_id}",
        )
        monitor_thread.start()

        self.send_signal_updates(
            data=None,
            description=f"Order placed for {symbol} (monitoring started)",
            fields={"orderId": order_id},
        )

    # ------------------------------------------------------------------
    # Candle alignment
    # ------------------------------------------------------------------

    def candlestick_aligner(self, interval_minutes: int = 15) -> None:
        """Sleep until the next candlestick boundary.

        For a 15-minute chart this means execution resumes at ``:00``,
        ``:15``, ``:30``, or ``:45``.

        Args:
            interval_minutes: Candle period in minutes.
        """
        now = get_indian_time()
        minutes = now.minute
        seconds = now.second

        if minutes % interval_minutes == 0:
            time.sleep(10)
            return

        next_block = (minutes // interval_minutes + 1) * interval_minutes
        sleep_sec = (next_block - minutes) * 60 - seconds

        if sleep_sec > 0:
            logger.info(
                f"Aligning to {interval_minutes}-minute candle. Sleeping {sleep_sec}s"
            )
            time.sleep(sleep_sec + 3)

    # ------------------------------------------------------------------
    # Thread workers
    # ------------------------------------------------------------------

    def refresh_active_positions(self) -> Dict[str, Dict[str, Any]]:
        """Return entry blocks with distinct active-position/cooldown states."""
        self.trades = self.trade_checker.activePosition_coolMaker()
        unavailable: Dict[str, Dict[str, Any]] = {}
        for symbol in self.trading_pairs:
            if symbol in self.trades:
                unavailable[symbol] = {
                    "reason": "active_position",
                    "position_side": self.trades[symbol].get("positionSide"),
                    "position_quantity": self.trades[symbol].get("quantity"),
                }
                continue
            if self.trade_checker.is_in_cooldown(symbol):
                unavailable[symbol] = {
                    "reason": "post_exit_cooldown",
                    "cooldown_until": self.trade_checker.get_cooldown(symbol),
                }
        return unavailable

    def start_signal_analysis(self) -> None:
        """Infinite loop: align to candle, generate signals, process them.

        This is the **main thread** entry-point called by :meth:`run`.
        """
        self.send_logs(data=None, description="Starting signal analysis thread")

        while True:
            try:
                self.candlestick_aligner(15)

                # Active broker positions are always unavailable for entry.
                # Retain configured post-trade cooldowns as an additional
                # safeguard against immediate re-entry after an exit.
                cooldown_list = self.refresh_active_positions()

                for signal in self.signal_analyzer.analyze_market(cooldown_list):
                    self.process_signal(signal)

                time.sleep(
                    SIGNAL_ANALYSIS_SLEEP - (time.time() % SIGNAL_ANALYSIS_SLEEP)
                )

            except Exception as e:
                self.handle_exception(
                    e, context_description="Exception in signal analysis thread"
                )
                time.sleep(120)

    def start_trade_checker(self) -> None:
        """Background trade-monitor thread entry-point."""
        self.send_logs(data=None, description="Starting trade checker thread")
        try:
            self.trades = self.trade_checker.activePosition_coolMaker()
            time.sleep(3)
            self.trade_checker.monitor_trades(
                self.trade_checker_pair,
                self.risk_management,
            )
        except Exception as e:
            self.handle_exception(
                e, context_description="Exception in trade checker thread"
            )

    def handle_crons(self) -> None:
        """Start the sentiment cron in a background daemon thread."""
        croner: Croner = self._croner or Croner()

        def cron_runner() -> None:
            try:
                croner.news_croner()
            except Exception as e:
                self.handle_exception(e, context_description="Exception in cron thread")

        cron_thread = threading.Thread(
            target=cron_runner, daemon=True, name="CronThread"
        )
        cron_thread.start()
        self.workers_to_monitor.append(cron_thread)

    # ------------------------------------------------------------------
    # Worker health monitor
    # ------------------------------------------------------------------

    def monitor_workers(self, check_interval: int = 300) -> None:
        """Periodically check worker threads and alert on failure.

        Args:
            check_interval: Seconds between health checks.
        """
        while True:
            for worker in self.workers_to_monitor:
                if not worker.is_alive():
                    self.send_alerts(
                        data=None, description=f"Worker {worker.name} has stopped!"
                    )
                    logger.error(f"Worker {worker.name} has stopped.")
                logger.info(f"Worker {worker.name} is alive.")
            time.sleep(check_interval)

    def report_performance(self, interval_seconds: int = 86400) -> None:
        """Sync Binance income and emit a fee-aware report once per day."""
        reporters = [
            PerformanceReporter(
                client,
                self.order_manager.mongo_handler,
                ExecutionMode(mode).value,
            )
            for mode, client in self.order_manager.futures_clients.items()
        ]
        while True:
            for reporter in reporters:
                try:
                    reporter.report_last_24_hours()
                except Exception as exc:
                    self.handle_exception(exc, "Exception in performance reporter")
            time.sleep(interval_seconds)

    # ------------------------------------------------------------------
    # Main runner
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start all trading threads and enter the signal-analysis loop."""
        logger.info("Starting Binance automation (thread-based)")

        monitor_thread = threading.Thread(
            target=self.monitor_workers, daemon=True, name="MonitorThread"
        )
        monitor_thread.start()

        self.handle_crons()

        try:
            reporter = self._testnet_reporter or TestnetDailyReporter.from_env(
                self.order_manager.mongo_handler,
                self.order_manager.futures_clients.get(ExecutionMode.TESTNET),
            )
        except Exception as exc:
            reporter = None
            self.handle_exception(
                exc, "Testnet reporting disabled because configuration is invalid"
            )
        if reporter is not None:
            report_thread = threading.Thread(
                target=reporter.run_forever,
                daemon=True,
                name="TestnetDailyReporterThread",
            )
            report_thread.start()
            self.workers_to_monitor.append(report_thread)

        trade_thread = threading.Thread(
            target=self.start_trade_checker, daemon=True, name="TradeCheckerThread"
        )
        trade_thread.start()
        self.workers_to_monitor.append(trade_thread)

        if self.order_manager.execution_settings.can_submit_orders:
            performance_thread = threading.Thread(
                target=self.report_performance,
                daemon=True,
                name="PerformanceReporterThread",
            )
            performance_thread.start()
            self.workers_to_monitor.append(performance_thread)

        logger.info("All automation threads started successfully")
        self.start_signal_analysis()


# =============================================================================
# Entry point
# =============================================================================


def main() -> None:
    """Create a :class:`BinanceAutomation` instance and run it."""
    automation = BinanceAutomation()

    # install global crash handler
    install_global_exception_handler(automation)

    try:
        automation.run()
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        logger.info("Stopping Binance Automation...")


if __name__ == "__main__":
    main()
