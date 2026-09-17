"""Periodic fee-aware performance reporting."""

from datetime import date, datetime, timedelta, timezone
import logging
from typing import Any

from orbit.core.discord_manager import DiscordManager
from orbit.core.performance import PerformanceTracker
from orbit.core.reporting import IST, report_window, snapshot_usdt_income

logger = logging.getLogger("Orbit")


class PerformanceReporter(DiscordManager):
    def __init__(
        self,
        futures_client: Any,
        mongo_handler: Any = None,
        execution_mode: str = "unknown",
    ) -> None:
        super().__init__()
        self.client = futures_client
        self.tracker = PerformanceTracker(
            futures_client, mongo_handler, execution_mode
        )

    def report_last_24_hours(self) -> dict[str, Any]:
        start = datetime.now(timezone.utc) - timedelta(hours=24)
        records = self.client.get_income_history(
            startTime=int(start.timestamp() * 1000), recvWindow=60000
        )
        if self.tracker.mongo_handler is not None:
            self.tracker.mongo_handler.store_income_records(
                records, self.tracker.execution_mode
            )
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

    def archive_recent_reports(self, today: date | None = None, days: int = 30) -> int:
        """Persist verified completed IST day and week cutoffs for later reports."""
        mongo = self.tracker.mongo_handler
        if mongo is None or self.tracker.execution_mode != "testnet":
            return 0
        today = today or datetime.now(IST).date()
        # A completed week can start six days before the oldest daily window.
        first_day = today - timedelta(days=days + 6)
        oldest_end_day = today - timedelta(days=days)
        first_start, _ = report_window(first_day)
        wallet, income = snapshot_usdt_income(
            PerformanceTracker(self.client), first_start
        )
        archived = 0

        def save(report_type: str, start: datetime, end: datetime) -> None:
            nonlocal archived
            start_ms = int(start.timestamp() * 1000)
            end_ms = int(end.timestamp() * 1000)
            period_income = [
                row for row in income if start_ms <= int(row["time"]) < end_ms
            ]
            subsequent = sum(
                float(row["income"])
                for row in income if int(row["time"]) >= end_ms
            )
            if mongo.store_report_accounting(
                report_type, start, end, "testnet", wallet - subsequent,
                period_income,
            ):
                archived += 1

        for offset in range(days + 6):
            day = first_day + timedelta(days=offset)
            start, end = report_window(day)
            if day >= oldest_end_day:
                save("daily", start, end)
            week_end_day = day + timedelta(days=7)
            if day.weekday() == 5 and oldest_end_day <= week_end_day <= today:
                _week_start, week_end = report_window(day, 7)
                save("weekly", start, week_end)
        return archived
