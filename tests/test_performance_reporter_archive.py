from datetime import date, datetime
from unittest.mock import MagicMock, patch

from orbit.core.performance_reporter import PerformanceReporter
from orbit.core.reporting import IST


@patch("orbit.core.performance_reporter.snapshot_usdt_income")
def test_archive_recent_reports_stores_completed_daily_and_weekly_wallets(
    snapshot: MagicMock,
) -> None:
    def stamp(day: int) -> int:
        return int(datetime(2026, 9, day, 12, tzinfo=IST).timestamp() * 1000)

    snapshot.return_value = (
        1115.0,
        [
            {"asset": "USDT", "income": "10", "time": stamp(12)},
            {"asset": "USDT", "income": "100", "time": stamp(18)},
            {"asset": "USDT", "income": "5", "time": stamp(19)},
        ],
    )
    mongo = MagicMock()
    reporter = PerformanceReporter(MagicMock(), mongo, "testnet")

    assert reporter.archive_recent_reports(date(2026, 9, 20), days=8) == 9

    weekly = [
        call.args for call in mongo.store_report_accounting.call_args_list
        if call.args[0] == "weekly"
    ]
    assert len(weekly) == 1
    report_type, start, end, mode, wallet, income = weekly[0]
    assert (report_type, mode, wallet) == ("weekly", "testnet", 1110.0)
    assert start == datetime(2026, 9, 12, tzinfo=IST)
    assert end == datetime(2026, 9, 19, tzinfo=IST)
    assert len(income) == 2

    september_12 = [
        call.args for call in mongo.store_report_accounting.call_args_list
        if call.args[0] == "daily" and call.args[1].date() == date(2026, 9, 12)
    ][0]
    assert september_12[4] == 1010.0
