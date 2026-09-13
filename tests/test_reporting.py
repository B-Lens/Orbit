from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from orbit.api import _report_accounting
from orbit.core.reporting import IST, report_metrics, report_window
from orbit.core.testnet_reporter import TestnetDailyReporter as DailyReporter, build_report_body, build_weekly_report_body


def test_september_12_atom_closes_fall_on_different_ist_days() -> None:
    decisions = [{
        "symbol": "ATOMUSDT",
        "execution_events": [
            {"status": "trade_closed", "timestamp": datetime(2026, 9, 12, 4, 10, 18), "pnl": -6.43758611},
            {"status": "trade_closed", "timestamp": datetime(2026, 9, 12, 23, 0, 43), "pnl": 15.15294538},
        ],
    }]
    september_12 = build_report_body(date(2026, 9, 12), decisions, [])
    september_13 = build_report_body(date(2026, 9, 13), decisions, [])
    assert "Closed trades: **1**" in september_12
    assert "| ATOMUSDT | 1 | -6.43758611 |" in september_12
    assert "2026-09-12T09:40:18+05:30" in september_12
    assert "Closed trades: **1**" in september_13
    assert "| ATOMUSDT | 1 | 15.15294538 |" in september_13
    assert "2026-09-13T04:30:43+05:30" in september_13


def test_ist_week_includes_start_and_excludes_end() -> None:
    start, end = report_window(date(2026, 9, 5), 7)
    assert start.astimezone(timezone.utc) == datetime(2026, 9, 4, 18, 30, tzinfo=timezone.utc)
    assert end.astimezone(timezone.utc) == datetime(2026, 9, 11, 18, 30, tzinfo=timezone.utc)
    rows = [
        {"timestamp": stamp, "outcome": "accepted"}
        for stamp in [datetime(2026, 9, 4, 18, 29, 59), datetime(2026, 9, 4, 18, 30), datetime(2026, 9, 11, 18, 29, 59), datetime(2026, 9, 11, 18, 30)]
    ]
    body = build_weekly_report_body(date(2026, 9, 5), rows, [], 1000)
    assert "Trade attempts: **2**" in body
    assert "2026-09-05T00:00:00+05:30" in body
    assert "2026-09-12T00:00:00+05:30" in body


def test_net_income_equity_and_drawdown_reconcile() -> None:
    income = [
        {"time": 3, "incomeType": "REALIZED_PNL", "income": "30"},
        {"time": 1, "incomeType": "REALIZED_PNL", "income": "20"},
        {"time": 2, "incomeType": "REALIZED_PNL", "income": "-10"},
        {"time": 2, "incomeType": "COMMISSION", "income": "-2"},
        {"time": 2, "incomeType": "FUNDING_FEE", "income": "-1"},
    ]
    metrics = report_metrics(income, 1037)
    assert metrics["net_pnl"] == 37
    assert metrics["opening_equity"] == 1000
    assert metrics["equity_value"] == 1037
    assert metrics["equity_change_pct"] == pytest.approx(3.7)
    assert metrics["max_drawdown"] == 13
    assert metrics["max_drawdown_pct"] == pytest.approx(13 / 1020 * 100)
    for body in (
        build_report_body(date(2026, 9, 12), [], income, cutoff_equity=1037),
        build_weekly_report_body(date(2026, 9, 5), [], income, 1037),
    ):
        assert "Net P&L: **37.00000000 USDT**" in body
        assert "Equity value: **1037.00000000 USDT**" in body
        assert "Equity change %: **3.70000000 %**" in body
        assert "Max drawdown: **13.00000000 USDT**" in body


def test_simultaneous_income_does_not_create_artificial_drawdown() -> None:
    records = [{"time": 1, "income": "-10"}, {"time": 1, "income": "20"}]
    assert report_metrics(records, 1010)["max_drawdown"] == 0
    assert report_metrics(reversed(records), 1010)["max_drawdown"] == 0
    assert report_metrics([], 0)["equity_change_pct"] is None


@patch.dict("os.environ", {"BINANCE_TESTNET_API_KEY": "test-only", "BINANCE_TESTNET_SECRET_KEY": "test-only"})
@patch("orbit.api._build_futures_client")
@patch("orbit.core.performance.PerformanceTracker.utc_now", return_value=datetime(2026, 9, 13, 2, tzinfo=IST))
def test_dashboard_reconstructs_period_equity_without_writes(_now: MagicMock, factory: MagicMock) -> None:
    start, end = report_window(date(2026, 9, 12))
    period = [{"time": int(start.timestamp() * 1000), "incomeType": "REALIZED_PNL", "income": "10"}]
    subsequent = [{"time": int(end.timestamp() * 1000), "incomeType": "REALIZED_PNL", "income": "25"}]
    client = factory.return_value
    client.account.return_value = {"totalWalletBalance": "1035"}
    client.get_income_history.side_effect = [period, subsequent]
    records, equity = _report_accounting(start, end)
    assert records == period
    assert equity == 1010
    assert client.get_income_history.call_args_list[0].kwargs["endTime"] == int(end.timestamp() * 1000) - 1
    assert {call[0] for call in client.method_calls} == {"account", "get_income_history"}


@patch("orbit.core.performance.PerformanceTracker.utc_now", return_value=datetime(2026, 9, 12, 2, tzinfo=IST))
def test_weekly_publisher_reconstructs_equity_and_persists_metrics(_now: MagicMock) -> None:
    start, end = report_window(date(2026, 9, 5), 7)
    period = [{"time": int(start.timestamp() * 1000), "incomeType": "REALIZED_PNL", "income": "10"}]
    mongo, client, github = MagicMock(), MagicMock(), MagicMock()
    mongo.get_trade_decisions.return_value = []
    mongo.get_income_records.return_value = period
    client.get_income_history.side_effect = [period, []]
    client.account.return_value = {"totalWalletBalance": "1010"}
    DailyReporter(mongo, github, client).publish_week(date(2026, 9, 5))
    mongo.get_trade_decisions.assert_called_once_with(start, end, "testnet", include_event_window=True)
    stored = mongo.store_finalized_report.call_args.args[0]
    assert stored["timezone"] == "IST"
    assert stored["metrics"]["equity_value"] == 1010
    assert stored["metrics"]["equity_change_pct"] == 1
    assert "Equity value: **1010.00000000 USDT**" in github.publish.call_args.args[1]


@patch("orbit.core.testnet_reporter.time_module.sleep", side_effect=RuntimeError("end test"))
@patch("orbit.core.testnet_reporter.datetime")
def test_scheduler_publishes_at_ist_saturday_midnight(clock: MagicMock, _sleep: MagicMock) -> None:
    clock.now.return_value = datetime(2026, 9, 11, 18, 30, tzinfo=timezone.utc).astimezone(IST)
    reporter = DailyReporter(MagicMock(), MagicMock())
    reporter.publish_date = MagicMock()
    reporter.publish_week = MagicMock()
    with pytest.raises(RuntimeError, match="end test"):
        reporter.run_forever()
    clock.now.assert_called_once_with(IST)
    reporter.publish_date.assert_called_once_with(date(2026, 9, 11))
    reporter.publish_week.assert_called_once_with(date(2026, 9, 5))
