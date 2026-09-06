"""
binance_ws_manager
==================

Provides :class:`BinanceWSManager`, a fault-tolerant WebSocket manager for
Binance Futures real-time ticker price feeds.

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

Fixes applied (2026-05-14)
--------------------------
1. ``update_pairs`` now acquires ``_lock`` before writing ``trading_pairs``
   to eliminate the torn-read race condition with ``_stream_url()``.
2. ``start()`` clears ``_stop_event`` *inside* the lock so the stale-checker
   never starts before the run-thread is ready.
3. ``_stale_thread`` is guarded against double-launch on rapid
   ``stop()``/``start()`` cycles.
4. Duplicate disconnect log entries suppressed via ``_notified_disconnect``
   flag — only ``_on_close`` emits the final status notification.
5. Empty ``trading_pairs`` detected at the top of ``_run_loop``; the loop
   aborts cleanly instead of spinning in backoff forever.
6. ``_STALE_CHECK_INTERVAL`` is now derived from ``stale_threshold`` (⌊threshold/3⌋,
   minimum 1 s) so short thresholds are always caught promptly.
7. ``_on_close`` type annotations corrected (``Optional[int]``,
   ``Optional[str]``).
8. ``_last_message_time`` writes are protected by ``_lock`` (belt-and-braces
   for non-CPython runtimes).
"""

import json
import logging
import threading
import time
from typing import Callable, List, Optional

import websocket

logger = logging.getLogger("Orbit")

# Suppress duplicate error logging from the websocket library itself.
logging.getLogger("websocket").setLevel(logging.CRITICAL)

_INITIAL_BACKOFF: float = 1.0    # seconds
_MAX_BACKOFF: float = 60.0       # seconds
_BACKOFF_FACTOR: float = 2.0     # exponential multiplier
_PING_INTERVAL: int = 30         # seconds between pings
_PING_TIMEOUT: int = 20          # seconds to wait for pong


class BinanceWSManager:
    """Fault-tolerant WebSocket manager for Binance Futures ticker streams.

    Args:
        trading_pairs: List of symbols to subscribe to (e.g. ``["BTCUSDT"]``).
        on_price_update: Callback invoked with ``(symbol, price, timestamp)``
            whenever a new ticker update arrives.
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
        stale_threshold: float = 30.0,
        ping_interval: int = _PING_INTERVAL,
        ping_timeout: int = _PING_TIMEOUT,
    ) -> None:
        self.trading_pairs = trading_pairs
        self._on_price_update = on_price_update
        self._on_status_change = on_status_change
        self.stale_threshold = stale_threshold
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout

        # Fix #6 — derive check interval from threshold so short thresholds
        # are always caught within one polling cycle.
        self._stale_check_interval: float = max(1.0, stale_threshold / 3)

        self._ws: Optional[websocket.WebSocketApp] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._stale_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        self._stop_event = threading.Event()
        self._last_message_time: float = 0.0
        self._connected: bool = False

        # Fix #4 — suppress duplicate log/callback on error+close pair
        self._notified_disconnect: bool = False

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
        # Fix #2 — clear stop_event inside the lock so the stale-checker
        # is never launched before the run-thread exists.
        with self._lock:
            if self._ws_thread and self._ws_thread.is_alive():
                logger.debug("[WSManager] Already running — ignoring start()")
                return

            # Safe to clear here: we hold the lock and the run-thread is not alive.
            self._stop_event.clear()

            self._ws_thread = threading.Thread(
                target=self._run_loop,
                name="BinanceWSManager-run",
                daemon=True,
            )
            self._ws_thread.start()

        # Fix #3 — guard stale-checker against double-launch.
        if not (self._stale_thread and self._stale_thread.is_alive()):
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
        # Fix #1 — acquire lock before writing to prevent torn reads in
        # _stream_url() which may be called concurrently from _run_loop.
        with self._lock:
            self.trading_pairs = trading_pairs
        self._close_ws()  # triggers reconnect in _run_loop

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _stream_url(self) -> str:
        # Called from _run_loop which already holds _lock at ws-creation time,
        # but _stream_url itself is cheap — just read under the lock.
        with self._lock:
            pairs = list(self.trading_pairs)
        # Ticker streams publish the latest contract price at a fixed cadence.
        # Trade streams only publish when a trade occurs, which made quiet
        # symbols appear stale and caused unnecessary REST fallbacks.
        streams = "/".join(f"{p.lower()}@ticker" for p in pairs)
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
        self._notified_disconnect = False  # reset for the new connection
        with self._lock:
            self._last_message_time = time.time()
        self._notify_status("WebSocket connection opened.")

    def _on_message(self, ws: websocket.WebSocketApp, raw: str) -> None:
        # Fix #8 — protect _last_message_time write (belt-and-braces for
        # non-CPython runtimes; no-op overhead on CPython due to the GIL).
        now = time.time()
        with self._lock:
            self._last_message_time = now
        try:
            msg = json.loads(raw)
            data = msg.get("data", {})
            symbol = data.get("s")
            price_str = data.get("c")
            if symbol and price_str:
                self._on_price_update(symbol, float(price_str), now)
            else:
                logger.debug(f"[WSManager] Unrecognised message format: {msg}")
        except Exception:
            logger.exception("[WSManager] Error parsing WebSocket message")

    def _on_error(self, ws: websocket.WebSocketApp, error: Exception) -> None:
        self._connected = False
        # Fix #4 — log the error once here, but do NOT call _notify_status
        # yet. _on_close always fires after _on_error, so we let _on_close
        # own the status notification to avoid duplicate callbacks.
        logger.Warning(f"[WSManager] WebSocket Issue : {error}")

    def _on_close(
        self,
        ws: websocket.WebSocketApp,
        close_status_code: Optional[int],   # Fix #7 — corrected type annotation
        close_msg: Optional[str],            # Fix #7 — corrected type annotation
    ) -> None:
        self._connected = False
        # Fix #4 — emit the single, authoritative disconnect status here.
        if not self._notified_disconnect:
            self._notified_disconnect = True
            self._notify_status(
                f"WebSocket closed (code={close_status_code}, msg={close_msg})"
            )

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

            # Fix #5 — abort cleanly instead of spinning when no pairs are set.
            with self._lock:
                pairs_snapshot = list(self.trading_pairs)

            if not pairs_snapshot:
                logger.error(
                    "[WSManager] No trading pairs configured — "
                    "run-loop will not start. Call update_pairs() first."
                )
                break

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
                    reconnect=0,  # we manage reconnects ourselves
                )
            except Exception:
                logger.exception("[WSManager] Unexpected exception in run_forever()")

            if self._stop_event.is_set():
                logger.info("[WSManager] Stop event set — exiting run-loop.")
                break

            # Reset backoff if the connection was alive long enough to be
            # considered successful (received at least one message after connect).
            with self._lock:
                last_msg = self._last_message_time
            if last_msg > connect_time:
                backoff = _INITIAL_BACKOFF

            logger.warning(f"[WSManager] Disconnected. Reconnecting in {backoff:.1f}s …")
            self._stop_event.wait(timeout=backoff)
            backoff = min(backoff * _BACKOFF_FACTOR, _MAX_BACKOFF)

        logger.info("[WSManager] Run-loop exited.")

    # ------------------------------------------------------------------
    # Stale-connection watchdog
    # ------------------------------------------------------------------

    def _stale_checker(self) -> None:
        """Background thread that restarts the WebSocket if it goes stale.

        The poll interval is ``max(1, stale_threshold / 3)`` seconds, so
        even short thresholds are caught within a single polling cycle
        (Fix #6).
        """
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self._stale_check_interval)

            if self._stop_event.is_set():
                break

            if not self._connected:
                continue

            with self._lock:
                last_msg = self._last_message_time

            age = time.time() - last_msg
            if age > self.stale_threshold:
                logger.warning(
                    f"[WSManager] No message received for {age:.1f}s "
                    f"(threshold={self.stale_threshold}s) — forcing reconnect."
                )
                self._close_ws()

        logger.info("[WSManager] Stale-checker exited.")
