from datetime import date, datetime, timezone

from orbit.core.reporting import (
    build_report_body,
    build_weekly_report_body,
    report_metrics,
    report_window,
)


def test_closed_lifecycle_records_produce_asset_performance() -> None:
    first = build_report_body(
        date(2026, 9, 12),
        [],
        [],
        closed_trades=[{"symbol": "ATOMUSDT", "pnl": -6.43758611}],
    )
    second = build_report_body(
        date(2026, 9, 13),
        [],
        [],
        closed_trades=[{"symbol": "ATOMUSDT", "pnl": 15.15294538}],
    )
    assert "| ATOMUSDT | 1 | -6.43758611 |" in first
    assert "| ATOMUSDT | 1 | 15.15294538 |" in second
    assert "Account-income performance" in first
    assert "exchange income ledger, not the closed-lifecycle table" in first


def test_closed_trade_summary_ignores_duplicate_decision_events() -> None:
    decisions = [{
        "symbol": "ATOMUSDT",
        "execution_events": [
            {"status": "trade_closed", "timestamp": datetime(2026, 9, 12, 4), "pnl": 4},
            {"status": "trade_closed", "timestamp": datetime(2026, 9, 12, 4), "pnl": 4},
        ],
    }]
    body = build_report_body(
        date(2026, 9, 12),
        decisions,
        [],
        closed_trades=[{"symbol": "ATOMUSDT", "pnl": 4}],
    )

    assert "Closed lifecycle records: **1**" in body
    assert "| ATOMUSDT | 1 | 4.00000000 |" in body


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
