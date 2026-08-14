"""Periodic fee-aware performance reporting."""

from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from orbit.core.discord_manager import DiscordManager
from orbit.core.performance import PerformanceTracker

logger = logging.getLogger("Orbit")


class PerformanceReporter(DiscordManager):
    def __init__(self, futures_client: Any, mongo_handler: Any = None) -> None:
        super().__init__()
        self.client = futures_client
        self.tracker = PerformanceTracker(futures_client, mongo_handler)

    def report_last_24_hours(self) -> dict[str, Any]:
        start = datetime.now(timezone.utc) - timedelta(hours=24)
        records = self.client.get_income_history(
            startTime=int(start.timestamp() * 1000), recvWindow=60000
        )
        if self.tracker.mongo_handler is not None:
            self.tracker.mongo_handler.store_income_records(records)
        wallet = float(self.client.account()["totalWalletBalance"])
        summary = self.tracker.summarize(records)
        starting_equity = wallet - summary.net_pnl
        summary = self.tracker.summarize(records, starting_equity=starting_equity)
        payload = {
            "window": "24h",
            "wallet_equity": wallet,
            **summary.to_dict(),
        }
        logger.info("24h performance: %s", payload)
        self.send_logs(
            data=None,
            description="Orbit Futures performance (last 24h)",
            fields=payload,
        )
        return payload
