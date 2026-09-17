from datetime import date, datetime, timezone

from orbit.core.reporting import (
    build_report_body,
    build_weekly_report_body,
    report_metrics,
    report_window,
    reconstruct_equity,
)
from unittest.mock import MagicMock
import pytest


def test_ist_day_assigns_closes_by_event_time() -> None:
    decisions = [{
        "symbol": "ATOMUSDT",
        "execution_events": [
            {"status": "trade_closed", "timestamp": datetime(2026, 9, 12, 4, 10, 18), "pnl": -6.43758611},
            {"status": "trade_closed", "timestamp": datetime(2026, 9, 12, 23, 0, 43), "pnl": 15.15294538},
        ],
    }]
    first = build_report_body(date(2026, 9, 12), decisions, [])
    second = build_report_body(date(2026, 9, 13), decisions, [])
    assert "| ATOMUSDT | 1 | -6.43758611 |" in first
    assert "| ATOMUSDT | 1 | 15.15294538 |" in second


def test_ist_week_includes_start_and_excludes_end() -> None:
    start, end = report_window(date(2026, 9, 5), 7)
    assert start.astimezone(timezone.utc) == datetime(2026, 9, 4, 18, 30, tzinfo=timezone.utc)
    assert end.astimezone(timezone.utc) == datetime(2026, 9, 11, 18, 30, tzinfo=timezone.utc)
    rows = [
        {"timestamp": stamp, "outcome": "accepted"}
        for stamp in [datetime(2026, 9, 4, 18, 29, 59), datetime(2026, 9, 4, 18, 30), datetime(2026, 9, 11, 18, 29, 59), datetime(2026, 9, 11, 18, 30)]
    ]
    assert "Trade attempts: **2**" in build_weekly_report_body(
        date(2026, 9, 5), rows, []
    )


def test_mongodb_income_does_not_invent_historical_equity() -> None:
    income = [
        {"time": 1, "incomeType": "REALIZED_PNL", "income": "20"},
        {"time": 2, "incomeType": "COMMISSION", "income": "-2"},
        {"time": 3, "incomeType": "FUNDING_FEE", "income": "-1"},
    ]
    metrics = report_metrics(income, None)
    assert metrics["net_pnl"] == 17
    assert metrics["equity_value"] is None
    assert metrics["opening_equity"] is None
    assert metrics["equity_change_pct"] is None
    assert "Equity value: **Unavailable**" in build_report_body(
        date(2026, 9, 12), [], income, include_automation_task=False
    )


def test_simultaneous_income_does_not_create_artificial_drawdown() -> None:
    records = [{"time": 1, "income": "-10"}, {"time": 1, "income": "20"}]
    assert report_metrics(records, None)["max_drawdown"] == 0
    assert report_metrics(reversed(records), None)["max_drawdown"] == 0


def test_reconstruct_equity_uses_verified_single_asset_wallet() -> None:
    tracker = MagicMock()
    tracker.utc_now.return_value = datetime(2026, 9, 17, tzinfo=timezone.utc)
    tracker.futures_client.account.return_value = {
        "multiAssetsMargin": False, "totalWalletBalance": "1127.0"
    }
    tracker.sync_window.return_value.net_pnl = 27.0
    tracker.last_records = [{"time": 1, "asset": "USDT", "income": "27"}]

    assert reconstruct_equity(
        tracker, datetime(2026, 9, 16, tzinfo=timezone.utc)
    ) == 1100.0


def test_reconstruct_equity_rejects_multi_asset_wallet() -> None:
    tracker = MagicMock()
    tracker.futures_client.account.return_value = {
        "multiAssetsMargin": True, "totalWalletBalance": "1127.0"
    }
    with pytest.raises(ValueError, match="single-asset"):
        reconstruct_equity(tracker, datetime(2026, 9, 16, tzinfo=timezone.utc))
    tracker.sync_window.assert_not_called()


def test_reconstruct_equity_retries_income_during_snapshot() -> None:
    tracker = MagicMock()
    snapshot_time = datetime(2026, 9, 17, tzinfo=timezone.utc)
    tracker.utc_now.return_value = snapshot_time
    tracker.futures_client.account.return_value = {
        "multiAssetsMargin": False, "totalWalletBalance": "1000"
    }
    tracker.sync_window.return_value.net_pnl = 10.0
    crossing = [{
        "time": int(snapshot_time.timestamp() * 1000),
        "asset": "USDT", "income": "10",
    }]
    tracker.last_records = crossing

    with pytest.raises(RuntimeError, match="every snapshot"):
        reconstruct_equity(
            tracker, datetime(2026, 9, 16, tzinfo=timezone.utc)
        )
    assert tracker.futures_client.account.call_count == 3
