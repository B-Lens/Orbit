"""
Binance Cryptocurrency Trading Automation System (Thread-Based Version)
=======================================================================

This module coordinates automated Binance Futures trading using a
multi-threaded architecture. The system handles:

- Market signal generation
- Automated order placement
- Trade monitoring & cooldown logic
- Scheduled sentiment analysis (cron-like executor)
- Discord notifications
- Exception & error management

Threading is used because:
- All operations are I/O-bound (network to Binance)
- Has lower overhead than multiprocessing
- Binance clients are NOT pickle-safe
- Much safer & simpler for long-running trading systems

Author: Pankaj Kumar
"""

import time
import os
import sys
import logging
import threading
from typing import List, Dict, Any

load_dotenv()  # Load environment variables from .env file

from config.config import load_config
from orbit.core.signal_analyzer import SignalAnalyzer
from orbit.core.trade_checker import TradeChecker
from orbit.core.order_manager import OrderManager
from orbit.core.authentication_manager import load_config
from orbit.core.exception_manager import ExceptionManager
from orbit.core.sentimen_cron import Croner
from orbit.utils.utils import *
from dotenv import load_dotenv

# Constants
SIGNAL_ANALYSIS_SLEEP = 900  # 15 minutes in seconds

# Load JSON configuration
config_json = load_config()
logger = logging.getLogger("Orbit")


# =============================================================================
# MAIN AUTOMATION ENGINE
# =============================================================================
class BinanceAutomation(ExceptionManager):
    """
    Main trading automation controller.

    Handles:
        - Running the signal analyzer (thread)
        - Placing orders based on signals
        - Monitoring order execution
        - Triggering trade checker (thread)
        - Running sentiment cron tasks (thread)
        - Tracking worker thread health
    """

    # ----------------------------------------------------------------------
    # Initialization
    # ----------------------------------------------------------------------
    def __init__(self):
        super().__init__()

        # Core components
        self.signal_analyzer = SignalAnalyzer()
        self.trade_checker = TradeChecker()
        self.order_manager = OrderManager()

        # Configuration
        self.trading_pairs: List[str] = config_json["trading_pairs"]
        self.trade_checker_pair: str = config_json["trade_checker_pair"]
        self.risk_management: Dict[str, Any] = config_json["risk_management"]
        self.future_leverage: int = config_json["FUTURE_LEVERAGE"]

        # Active trade map (thread local instance)
        self.trades: Dict[str, Any] = {}

        # Track all running worker threads for optional monitoring
        self.workers_to_monitor: List[threading.Thread] = []

        logger.info("BinanceAutomation initialized")

    # ----------------------------------------------------------------------
    # ORDER EXECUTION MONITOR (Thread)
    # ----------------------------------------------------------------------
    def monitor_order_execution(self, symbol: str, order_id: int, action: str, quantity: float, price: float):
        """
        Monitor order execution for up to 10 minutes. Runs in a dedicated thread.

        Handles:
            - Order status updates
            - Cancellation on timeout
            - Updating trade_checker cooldown
            - Recording executed trades
        """
        start_time = time.time()
        timeout_seconds = 600  # 10 minutes

        while time.time() - start_time < timeout_seconds:
            try:
                orders = self.order_manager.future_client.get_orders(symbol=symbol, orderId=order_id)

                # Orders can come as list or dict depending on library
                order_list = orders if isinstance(orders, list) else [orders]

                for order in order_list:
                    status = order.get("status")

                    if status in ("NEW"):
                        break

                    # Failed states
                    if status in ("CANCELED", "REJECTED", "EXPIRED"):
                        self.send_alerts(
                            data=None,
                            description=f"Order {order_id} for {symbol} was {status.lower()}",
                            fields=order,
                        )
                        return

                    # Success state
                    if status == "FILLED":
                        self.trade_checker.set_cooldown(symbol)
                        self.trades[symbol] = {
                            "symbol": symbol,
                            "positionSide": action,
                            "quantity": quantity,
                            "orderId": order_id,
                            "price": price,
                        }
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
                    context_description=f"Exception monitoring order {order_id} for {symbol}"
                )
                time.sleep(30)

        # TIMEOUT → Cancel order
        try:
            cancel_result = self.order_manager.cancel_order(symbol, order_id)
            self.send_alerts(
                data=None,
                description=f"Order {order_id} for {symbol} cancelled (10 min timeout)",
                fields=cancel_result,
            )
            assert cancel_result.get("status") == "CANCELED", "Cancellation failed"
        except Exception as e:
            self.handle_exception(
                e,
                context_description=f"Exception cancelling timed-out order {order_id} for {symbol}",
            )

    # ----------------------------------------------------------------------
    # SIGNAL PROCESSOR
    # ----------------------------------------------------------------------
    def process_signal(self, signal: Dict[str, Any]):
        """
        Process trading signals (BUY/SELL) and execute trades accordingly.

        Workflow:
            • Place order
            • Start order monitor thread
        """
        if not signal:
            return
        symbol = signal["symbol"]
        action = signal["signal"]
        entry_price = signal["entry_price"]
        stop_loss = signal["stop_loss"]
        target = signal["take_profit"]
        meta_info = signal.get("Other Info", "")

        if meta_info:
            self.send_logs(
                data=f"{symbol} - {action}",
                description=f"Signal Info: {meta_info}",
                fields=None,
            )

        # Current price (if not in signal)
        price_to_use = entry_price or self.order_manager.get_symbol_price(symbol)

        # BTC special leverage
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
        )

        time.sleep(0.5)

        if not order_response:
            self.send_alerts(
                data=None,
                description=f"Order failed for {symbol}",
            )
            return

        order_id = order_response.get("orderId")
        monitor_thread = threading.Thread(
            target=self.monitor_order_execution,
            args=(symbol, order_id, action, quantity, order_request["price"]),
            daemon=True,
            name=f"OrderMonitor-{symbol}-{order_id}",
        )
        monitor_thread.start()

        self.send_signal_updates(
            data=None,
            description=f"Order placed for {symbol} (monitoring started)",
            fields={"orderId": order_id},
        )

    # ----------------------------------------------------------------------
    # CANDLE ALIGNMENT
    # ----------------------------------------------------------------------
    def candlestick_aligner(self, interval_minutes: int = 15):
        """
        Aligns execution to candlestick boundaries.
        Example: For 15-min chart, run exactly at 00, 15, 30, 45.
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
            logger.info(f"Aligning to {interval_minutes}-minute candle. Sleeping {sleep_sec}s")
            time.sleep(sleep_sec + 3)

    # ----------------------------------------------------------------------
    # THREAD WORKERS
    # ----------------------------------------------------------------------
    def start_signal_analysis(self):
        """
        Continuously:
            - Align to candle boundaries
            - Read cooldowns
            - Run signal analyzer
            - Process new signals
        """
        self.send_logs(data=None, description="Starting signal analysis thread")

        while True:
            try:
                self.candlestick_aligner(15)

                # Load cooldown info
                self.trades = self.trade_checker.activePosition_coolMaker()
                cooldown_list = [
                    s for s in self.trading_pairs if self.trade_checker.is_in_cooldown(s)
                ]

                # Generate signals
                for signal in self.signal_analyzer.analyze_market(cooldown_list):
                    self.process_signal(signal)                    

                # Sleep until next quarter
                time.sleep(SIGNAL_ANALYSIS_SLEEP - (time.time() % SIGNAL_ANALYSIS_SLEEP))

            except Exception as e:
                self.handle_exception(
                    e, context_description="Exception in signal analysis thread"
                )
                time.sleep(120)

    def start_trade_checker(self):
        """Background trade monitor thread."""
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

    def handle_crons(self):
        """Run scheduled sentiment cron in background thread."""
        croner = Croner()

        def cron_runner():
            try:
                croner.sentiment_croner()
            except Exception as e:
                self.handle_exception(
                    e, context_description="Exception in cron thread"
                )

        cron_thread = threading.Thread(target=cron_runner, daemon=True, name="CronThread")
        cron_thread.start()
        self.workers_to_monitor.append(cron_thread)

    # ----------------------------------------------------------------------
    # OPTIONAL WORKER MONITOR
    # ----------------------------------------------------------------------
    def monitor_workers(self, check_interval=300):
        """Check if workers are alive and send alerts if any stop."""
        while True:
            for worker in self.workers_to_monitor:
                if not worker.is_alive():
                    self.send_alerts(
                        data=None,
                        description=f"Worker {worker.name} has stopped!",
                    )
                    logger.error(f"Worker {worker.name} has stopped.")
                logger.info(f"Worker {worker.name} is alive.")
            time.sleep(check_interval)

    # ----------------------------------------------------------------------
    # MAIN RUNNER
    # ----------------------------------------------------------------------
    def run(self):
        """Start all trading threads."""
        logger.info("Starting Binance automation (thread-based)")

        # Monitor thread
        monitor_thread = threading.Thread(
            target=self.monitor_workers, daemon=True, name="MonitorThread"
        )

        monitor_thread.start()
        # Cron thread
        self.handle_crons()

        # Trade checker thread
        trade_thread = threading.Thread(
            target=self.start_trade_checker, daemon=True, name="TradeCheckerThread"
        )
        trade_thread.start()
        self.workers_to_monitor.append(trade_thread)

        logger.info("All automation threads started successfully")
        self.start_signal_analysis()


# =============================================================================
# ENTRY POINT
# =============================================================================
def main():
    automation = BinanceAutomation()
    try:
        automation.run()
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        logger.info("Stopping Binance Automation...")


if __name__ == "__main__":
    main()
