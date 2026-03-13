import traceback

import logging
from orbit.core.discord_manager import DiscordManager

logger = logging.getLogger("Orbit")

class ExceptionManager(DiscordManager):
    def __init__(self, logger: logging.Logger = logger) -> None:
        """
        Initialize the ExceptionManager.
        """
        super().__init__()
        self.logger = logger

    def clientExceptionHandler(self, symbol, error, Location=None, msg=None):
        """
        Handles an exception, extracts traceback details, and sends/logs the exception.
        """

        # Full traceback as a nice string
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

        # Optionally still compute origin_file/origin_line if you want
        tb = traceback.extract_tb(error.__traceback__)
        origin_file = tb[-1].filename if tb else "unknown"
        origin_line = tb[-1].lineno if tb else -1

        # Send the full traceback as part of description
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
        self, exception, context_description, debug_params=None
    ):
        """
        Handles an exception, extracts traceback details, and sends/logs the exception.
        :param exception: The exception object.
        :param context_description: A description of where the exception occurred.
        :return: None
        """
        # Extract traceback details
        tb = traceback.extract_tb(exception.__traceback__)
        origin_file = tb[-1].filename if tb else "unknown"
        origin_line = tb[-1].lineno if tb else -1

        # Full traceback as a nice string
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


        # Prepare the exception description
        exception_message = (
            f"{context_description}: {exception} "
            f"(File: {origin_file}, Line: {origin_line})"
        )

        # Log or send the exception details
        logger.error(exception_message)

        self.exception_trigger(
            data=None,
            description=(
                f"Exception message: {exception_message} \n",
                f"File Location: {origin_file}, File Line: {origin_line}, "
                f"status: {getattr(exception, 'status_code', None)}, "
                f"error code: {getattr(exception, 'error_code', None)}, "
                f"error message: {getattr(exception, 'error_message', None)}\n\n"
                f"Full traceback:\n{traceback_str}"
            ),
        )

        # Send debug parameters if provided
        if debug_params:      
            self.send_exception_params_debug(
                data=None, description=context_description, fields=debug_params
            )

    def exception_trigger(self, data, description):
        """
        Mock method to send exception details to an external service.
        :param data: Optional data to send with the exception.
        :param description: The exception description.
        :return: None
        """
        self.send_exception(data=data, description=description)
