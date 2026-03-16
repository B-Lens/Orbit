"""
contradict_simulator
====================

Provides :class:`ContradictSimulator`, which runs in a background thread
and simulates skipped (contradict) trades to track whether they would have
hit their Stop Loss or Target.

The simulator polls Binance for live price data and updates the simulation
result in MongoDB once an outcome is determined.
"""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger("Orbit")

# Maximum time (seconds) to wait for a simulated trade to resolve
SIMULATION_TIMEOUT_SECONDS = 60 * 60 * 24  # 2 days
POLL_INTERVAL_SECONDS = 30


def _fetch_current_price(symbol: str) -> Optional[float]:
    """Fetch the latest mark price for *symbol* from Binance Futures REST API.

    Returns:
        Current price as a float, or ``None`` on failure.
    """
    url = "https://fapi.binance.com/fapi/v1/ticker/price"
    try:
        response = requests.get(url, params={"symbol": symbol}, timeout=10)
        response.raise_for_status()
        return float(response.json()["price"])
    except Exception as exc:
        logger.warning(f"[ContradictSimulator] Failed to fetch price for {symbol}: {exc}")
        return None


class ContradictSimulator:
    """Simulates a skipped contradict trade in a background thread.

    For each trade submitted via :meth:`simulate`, a daemon thread is
    spawned that polls the current price every :data:`POLL_INTERVAL_SECONDS`
    seconds until one of the following outcomes is reached:

    * **Target** – price reaches or crosses ``take_profit``.
    * **SL** – price reaches or crosses ``stop_loss``.
    * **Timeout** – :data:`SIMULATION_TIMEOUT_SECONDS` elapsed without resolution.

    Results are persisted to MongoDB via the supplied *mongo_handler*.

    Args:
        mongo_handler: A :class:`~orbit.core.mongo_handler.MongoHandler`
            instance used to persist simulation results.
    """

    def __init__(self, mongo_handler: Any) -> None:
        self.mongo_handler = mongo_handler

    def simulate(self, trade_info: Dict[str, Any]) -> None:
        """Spawn a daemon thread to simulate *trade_info*.

        Args:
            trade_info: Dictionary containing at minimum:

                * ``symbol`` – trading pair string.
                * ``signal`` – ``"BUY"`` or ``"SELL"``.
                * ``entry_price`` – float.
                * ``stop_loss`` – float.
                * ``take_profit`` – float.
                * ``sentiment`` – the contradicting sentiment string.
                * ``timestamp`` – trade timestamp.
        """
        thread = threading.Thread(
            target=self._run_simulation,
            args=(trade_info,),
            daemon=True,
            name=f"SimThread-{trade_info.get('symbol', 'UNKNOWN')}",
        )
        thread.start()
        logger.info(
            f"[ContradictSimulator] Started simulation thread for "
            f"{trade_info.get('symbol')} | signal={trade_info.get('signal')}"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_simulation(self, trade_info: Dict[str, Any]) -> None:
        """Poll price and determine outcome; persist result to MongoDB."""
        symbol: str = trade_info.get("symbol", "UNKNOWN")
        signal: str = trade_info.get("signal", "BUY")
        entry_price: float = float(trade_info.get("entry_price") or 0)
        stop_loss: float = float(trade_info.get("stop_loss") or 0)
        take_profit: float = float(trade_info.get("take_profit") or 0)
        sentiment: str = trade_info.get("sentiment", "")
        timestamp = trade_info.get("timestamp", datetime.now(timezone.utc))

        if entry_price == 0 or stop_loss == 0 or take_profit == 0:
            logger.warning(
                f"[ContradictSimulator] Incomplete trade info for {symbol}; skipping simulation."
            )
            return

        is_long = signal == "BUY"
        start_time = time.time()
        outcome = "Timeout"

        logger.info(
            f"[ContradictSimulator] Simulating {symbol} | "
            f"entry={entry_price} sl={stop_loss} tp={take_profit} side={signal}"
        )

        while True:
            elapsed = time.time() - start_time
            if elapsed >= SIMULATION_TIMEOUT_SECONDS:
                outcome = "Timeout"
                logger.info(f"[ContradictSimulator] {symbol} simulation timed out.")
                break

            current_price = _fetch_current_price(symbol)
            if current_price is None:
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            if is_long:
                if current_price <= stop_loss:
                    outcome = "SL"
                    break
                if current_price >= take_profit:
                    outcome = "Target"
                    break
            else:  # SHORT
                if current_price >= stop_loss:
                    outcome = "SL"
                    break
                if current_price <= take_profit:
                    outcome = "Target"
                    break

            time.sleep(POLL_INTERVAL_SECONDS)

        logger.info(f"[ContradictSimulator] {symbol} simulation outcome: {outcome}")

        self.mongo_handler.store_simulated_trade_result(
            symbol=symbol,
            signal=signal,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            sentiment=sentiment,
            outcome=outcome,
            trade_timestamp=timestamp,
            duration_seconds=int(time.time() - start_time),
        )
