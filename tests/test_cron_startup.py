import threading
from unittest.mock import MagicMock, patch

from orbit.core.main import BinanceAutomation


def test_mongodb_failure_in_cron_does_not_abort_service_startup() -> None:
    automation = BinanceAutomation.__new__(BinanceAutomation)
    automation._croner = None
    automation.workers_to_monitor = []
    failure_handled = threading.Event()
    automation.handle_exception = MagicMock(
        side_effect=lambda *args, **kwargs: failure_handled.set()
    )

    with patch("orbit.core.main.Croner", side_effect=ConnectionError("Connection refused")):
        automation.handle_crons()

    assert len(automation.workers_to_monitor) == 1
    assert automation.workers_to_monitor[0].name == "CronThread"
    assert failure_handled.wait(timeout=1)
    automation.handle_exception.assert_called_once()
    assert automation.handle_exception.call_args.kwargs["context_description"] == (
        "Exception in cron thread"
    )
