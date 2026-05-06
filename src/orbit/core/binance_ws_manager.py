"""
binance_ws_manager
==================

Provides :class:`BinanceWSManager`, a fault-tolerant WebSocket manager for
Binance Futures real-time trade price feeds.

Features
--------
* **Auto-reconnect** with exponential backoff (capped at 60 s).
* **Ping/pong keepalive** via ``websocket-client`` built-in support.
* **Stale-connection detection** — restarts if no message is received within
  ``stale_threshold`` seconds (default 30 s).
* **Thread safety** — guarantees only one WebSocket thread runs at a time
  using a :class:`threading.Lock`.
* **Clean shutdown** — :meth:`stop` signals the run-loop to exit and closes
  the underlying socket gracefully.
"""

import json
import logging
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

import websocket

logger = logging.getLogger("Orbit")

# Suppress verbose ERROR messages from the underlying websocket-client
# library so that routine disconnections do not flood the application
# logs with ``Connection to remote host was lost. - goodbye``.
logging.getLogger("websocket").setLevel(logging.WARNING)

_INITIAL_BACKOFF: float = 1.0       # seconds
_MAX_BACKOFF: float = 60.0          # seconds
_BACKOFF_FACTOR: float = 2.0        # exponential multiplier
_PING_INTERVAL: int = 30            # seconds between pings (increased from 20)
_PING_TIMEOUT: int = 20             # seconds to wait for pong (increased from 10)
_STALE_THRESHOLD: float = 30.0      # seconds before a connection is considered stale (increased from 5)
_STALE_CHECK_INTERVAL: float = 10.0 # how often the stale-checker polls (increased from 2)


class BinanceWSManager:
    """Fault-tolerant WebSocket manager for Binance Futures trade streams.

    Args:
        trading_pairs: List of symbols to subscribe to (e.g. ``["BTCUSDT"]``).
        on_price_update: Callback invoked with ``(symbol, price, timestamp)``
            whenever a new trade tick arrives.
        on_status_change: Optional callback invoked with a human-readable
            status string on connect / disconnect / error events.
        stale_threshold: Seconds without a message before the connection is
            considered stale and forcibly restarted.
        ping_interval: Seconds between WebSocket ping frames.
        ping_timeout: Seconds to wait for a pong before treating the
            connection as dead.
    """

    def __init__(
        self,
        trading_pairs: List[str],
        on_price_update: Callable[[str, float, float], None],
        on_status_change: Optional[Callable[[str], None]] = None,
        stale_threshold: float = _STALE_THRESHOLD,
        ping_interval: int = _PING_INTERVAL,
        ping_timeout: int = _PING_TIMEOUT,
    ) -> None:
        self.trading_pairs = trading_pairs
        self._on_price_update = on_price_update
        self._on_status_change = on_status_change
        self.stale_threshold = stale_threshold
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout

        self._ws: Optional[websocket.WebSocketApp] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._stale_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        self._stop_event = threading.Event()
        self._last_message_time: float = 0.0
        self._connected: bool = False

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        """``True`` while the WebSocket is open and receiving messages."""
        return self._connected

    def start(self) -> None:
        """Start the WebSocket manager in background daemon threads.

        Safe to call multiple times — subsequent calls are no-ops if the
        manager is already running.
        """
        if self._stop_event.is_set():
            # Allow restart after a previous stop()
            self._stop_event.clear()

        with self._lock:
            if self._ws_thread and self._ws_thread.is_alive():
                logger.debug("[WSManager] Already running — ignoring start()")
                return

            self._ws_thread = threading.Thread(
                target=self._run_loop,
                name="BinanceWSManager-run",
                daemon=True,
            )
            self._ws_thread.start()

        self._stale_thread = threading.Thread(
            target=self._stale_checker,
            name="BinanceWSManager-stale",
            daemon=True,
        )
        self._stale_thread.start()
        logger.info("[WSManager] Started.")

    def stop(self) -> None:
        """Signal the manager to stop and close the WebSocket gracefully."""
        logger.info("[WSManager] Stop requested.")
        self._stop_event.set()
        self._close_ws()

    def update_pairs(self, trading_pairs: List[str]) -> None:
        """Replace the subscribed trading pairs and reconnect immediately.

        Args:
            trading_pairs: New list of symbols.
        """
        logger.info(f"[WSManager] Updating trading pairs to {trading_pairs}")
        self.trading_pairs = trading_pairs
        self._close_ws()  # triggers reconnect in _run_loop

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _stream_url(self) -> str:
        streams = "/".join(f"{p.lower()}@trade" for p in self.trading_pairs)
        return f"wss://fstream.binance.com/stream?streams={streams}"

    def _notify_status(self, msg: str) -> None:
        logger.info(f"[WSManager] {msg}")
        if self._on_status_change:
            try:
                self._on_status_change(msg)
            except Exception:
                logger.exception("[WSManager] on_status_change callback raised")

    def _close_ws(self) -> None:
        """Close the current WebSocket connection without stopping the run-loop."""
        ws = self._ws
        if ws:
            try:
                ws.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # WebSocket callbacks
    # ------------------------------------------------------------------

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        self._connected = True
        self._last_message_time = time.time()
        self._notify_status("WebSocket connection opened.")

    def _on_message(self, ws: websocket.WebSocketApp, raw: str) -> None:
        self._last_message_time = time.time()
        try:
            msg = json.loads(raw)
            data = msg.get("data", {})
            symbol = data.get("s")
            price_str = data.get("p")
            if symbol and price_str:
                self._on_price_update(symbol, float(price_str), self._last_message_time)
            else:
                logger.debug(f"[WSManager] Unrecognised message format: {msg}")
        except Exception:
            logger.exception("[WSManager] Error parsing WebSocket message")

    def _on_error(self, ws: websocket.WebSocketApp, error: Exception) -> None:
        self._connected = False
        self._notify_status(f"WebSocket error: {error}")

    def _on_close(self, ws: websocket.WebSocketApp, close_status_code, close_msg) -> None:
        self._connected = False
        self._notify_status(f"WebSocket closed (code={close_status_code}, msg={close_msg})")

    # ------------------------------------------------------------------
    # Run-loop with exponential backoff
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        """Persistent reconnect loop with exponential backoff.

        Backoff resets to ``_INITIAL_BACKOFF`` after every successful
        connection (i.e. one that stays up long enough to receive at least
        one message).
        """
        backoff = _INITIAL_BACKOFF

        while not self._stop_event.is_set():
            url = self._stream_url()
            logger.info(f"[WSManager] Connecting to {url}")

            ws = websocket.WebSocketApp(
                url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )

            with self._lock:
                self._ws = ws

            connect_time = time.time()

            try:
                ws.run_forever(
                    ping_interval=self.ping_interval,
                    ping_timeout=self.ping_timeout,
                    reconnect=0,  # disable websocket-client's own reconnect; we handle it
                )
            except Exception as exc:
                # ``run_forever`` raises when the underlying socket fails.
                # This is a normal part of the reconnect logic; log at INFO
                # level so that it does not show up as an ERROR in production
                # dashboards.
                logger.info("[WSManager] run_forever ended (%s)", exc)

            if self._stop_event.is_set():
                logger.info("[WSManager] Stop event set — exiting run-loop.")
                break

            # Reset backoff if the connection was alive long enough to be
            # considered successful (received at least one message after connect).
            if self._last_message_time > connect_time:
                backoff = _INITIAL_BACKOFF

            logger.warning(f"[WSManager] Disconnected. Reconnecting in {backoff:.1f}s …")
            self._stop_event.wait(timeout=backoff)
            backoff = min(backoff * _BACKOFF_FACTOR, _MAX_BACKOFF)

        logger.info("[WSManager] Run-loop exited.")

    # ------------------------------------------------------------------
    # Stale-connection watchdog
    # ------------------------------------------------------------------

    def _stale_checker(self) -> None:
        """Background thread that restarts the WebSocket if it goes stale."""
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=_STALE_CHECK_INTERVAL)

            if self._stop_event.is_set():
                break

            if not self._connected:
                continue

            age = time.time() - self._last_message_time
            if age > self.stale_threshold:
                logger.warning(
                    f"[WSManager] No message received for {age:.1f}s "
                    f"(threshold={self.stale_threshold}s) — forcing reconnect."
                )
                self._close_ws()

        logger.info("[WSManager] Stale-checker exited.")
