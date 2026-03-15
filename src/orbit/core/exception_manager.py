"""
exception_manager
=================

Provides :class:`ExceptionManager`, a mixin that adds structured exception
handling and Discord-based error reporting on top of :class:`DiscordManager`.

Every core class that needs to report errors inherits from this class
(directly or via :class:`AuthenticationManager`).
"""

import traceback
import logging
from typing import Any, Dict, Optional, Union

from orbit.core.discord_manager import DiscordManager

logger = logging.getLogger("Orbit")


class ExceptionManager(DiscordManager):
    """Centralised exception handling with Discord webhook reporting.

    Args:
        custom_logger: An optional :class:`logging.Logger` instance.  When
            supplied it is stored as ``self.logger`` for sub-class use;
            the module-level ``logger`` is always used for ``error()`` calls
            inside this class.
    """

    def __init__(self, custom_logger: Optional[logging.Logger] = None) -> None:
        super().__init__()
        self.logger = custom_logger

    def clientExceptionHandler(
        self,
        symbol: Optional[str],
        error: Exception,
        Location: Optional[str] = None,
        msg: Optional[str] = None,
    ) -> None:
        """Handle a :class:`binance.error.ClientError` (or similar).

        Extracts the full traceback, logs it, and forwards the details to the
        *exception* Discord webhook.

        Args:
            symbol: The trading pair that triggered the error (may be ``None``).
            error: The caught exception instance.
            Location: Human-readable description of where the error occurred.
            msg: Optional extra context message.
        """
        traceback_str = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )

        logger.error(
            "Found Exception. symbol: %s, status: %s, error code: %s, error message: %s\nFull traceback:\n%s",
            symbol,
            getattr(error, "status_code", None),
            getattr(error, "error_code", None),
            getattr(error, "error_message", None),
            traceback_str,
        )

        tb = traceback.extract_tb(error.__traceback__)
        origin_file = tb[-1].filename if tb else "unknown"
        origin_line = tb[-1].lineno if tb else -1

        self.exception_trigger(
            data=None,
            description=(
                f"Found error. Symbol: {symbol}, [{msg}] "
                f"File Location: {origin_file}, File Line: {origin_line}, "
                f"status: {getattr(error, 'status_code', None)}, "
                f"error code: {getattr(error, 'error_code', None)}, "
                f"error message: {getattr(error, 'error_message', None)}\n\n"
                f"Full traceback:\n{traceback_str}"
            ),
        )

    def handle_exception(
        self,
        exception: Exception,
        context_description: str,
        debug_params: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Handle a generic exception with full traceback reporting.

        Args:
            exception: The caught exception instance.
            context_description: Human-readable context of where the error
                occurred (e.g. ``"ensure_orders"``).
            debug_params: Optional dictionary of debugging values that will be
                sent to the *exception_params* webhook.
        """
        tb = traceback.extract_tb(exception.__traceback__)
        origin_file = tb[-1].filename if tb else "unknown"
        origin_line = tb[-1].lineno if tb else -1

        traceback_str = "".join(
            traceback.format_exception(type(exception), exception, exception.__traceback__)
        )

        logger.error(
            "Found Exception. status: %s, error code: %s, error message: %s\nFull traceback:\n%s",
            getattr(exception, "status_code", None),
            getattr(exception, "error_code", None),
            getattr(exception, "error_message", None),
            traceback_str,
        )

        exception_message = (
            f"{context_description}: {exception} "
            f"(File: {origin_file}, Line: {origin_line})"
        )

        logger.error(exception_message)

        self.exception_trigger(
            data=None,
            description=(
                f"Exception message: {exception_message} \n"
                f"File Location: {origin_file}, File Line: {origin_line}, "
                f"status: {getattr(exception, 'status_code', None)}, "
                f"error code: {getattr(exception, 'error_code', None)}, "
                f"error message: {getattr(exception, 'error_message', None)}\n\n"
                f"Full traceback:\n{traceback_str}"
            ),
        )

        if debug_params:
            self.send_exception_params_debug(
                data=None, description=context_description, fields=debug_params
            )

    def exception_trigger(
        self,
        data: Optional[str],
        description: Union[str, tuple],
    ) -> None:
        """Forward exception details to the *exception* Discord webhook.

        Args:
            data: Optional plain-text content.
            description: The exception description (may be a tuple of strings
                produced by the caller; they are joined before sending).
        """
        if isinstance(description, tuple):
            description = "".join(str(s) for s in description)
        self.send_exception(data=data, description=description)