"""Shared IST calendar and wallet-income performance calculations."""

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional

from orbit.core.performance import PerformanceTracker

IST = timezone(timedelta(hours=5, minutes=30), name="IST")


def report_window(day: date, days: int = 1) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=IST)
    return start, start + timedelta(days=days)


def report_metrics(
    income: Iterable[Mapping[str, Any]], cutoff_equity: Optional[float]
) -> dict[str, Any]:
    records = sorted(income, key=lambda row: int(row.get("time", 0) or 0))
    summary = PerformanceTracker.summarize(dict(row) for row in records)
    opening = cutoff_equity - summary.net_pnl if cutoff_equity is not None else None
    # Income at one exchange timestamp is one balance change; order within a
    # millisecond must not manufacture a temporary peak or trough.
    changes: dict[int, float] = {}
    for row in records:
        stamp = int(row.get("time", 0) or 0)
        changes[stamp] = changes.get(stamp, 0.0) + float(row.get("income", 0) or 0)
    balance = peak = opening if opening is not None else 0.0
    drawdown = drawdown_pct = 0.0
    for change in changes.values():
        balance += change
        peak = max(peak, balance)
        drawdown = max(drawdown, peak - balance)
        if opening is not None and peak > 0:
            drawdown_pct = max(drawdown_pct, (peak - balance) / peak * 100)
    return {
        **summary.to_dict(),
        "opening_equity": opening,
        "equity_value": cutoff_equity,
        "equity_change_pct": summary.net_pnl / opening * 100 if opening and opening > 0 else None,
        "max_drawdown": drawdown,
        "max_drawdown_pct": drawdown_pct if opening is not None and opening > 0 else None,
    }


def reconstruct_equity(tracker: PerformanceTracker, end: datetime, attempts: int = 3) -> float:
    """Reconstruct cutoff wallet balance using a snapshot with no crossing income."""
    for _ in range(attempts):
        before = int(tracker.utc_now().timestamp() * 1000)
        account = tracker.futures_client.account()
        if account.get("multiAssetsMargin") is not False:
            raise ValueError("USDT wallet balance requires verified single-asset mode")
        after = int(tracker.utc_now().timestamp() * 1000)
        subsequent = tracker.sync_window(int(end.timestamp() * 1000), after + 1)
        if any(row.get("asset") != "USDT" for row in tracker.last_records):
            raise ValueError("Non-USDT income cannot reconstruct a USDT wallet balance")
        if not any(before <= int(row.get("time", 0) or 0) <= after for row in tracker.last_records):
            return float(account["totalWalletBalance"]) - subsequent.net_pnl
    raise RuntimeError("Account income changed during every snapshot; refusing to publish inconsistent equity")


def performance_lines(metrics: Mapping[str, Any]) -> list[str]:
    def value(key: str, unit: str) -> str:
        number = metrics[key]
        return "Unavailable" if number is None else f"{number:.8f} {unit}"

    lines = [
        "## Account performance", "",
        f"- Net P&L: **{value('net_pnl', 'USDT')}**",
        f"- Equity value: **{value('equity_value', 'USDT')}**",
        f"- Opening equity: **{value('opening_equity', 'USDT')}**",
        f"- Equity change %: **{value('equity_change_pct', '%')}**",
        f"- Max drawdown: **{value('max_drawdown', 'USDT')}**",
        f"- Max drawdown %: **{value('max_drawdown_pct', '%')}**", "",
        "### Net P&L breakdown", "",
        f"- Realized P&L: **{value('realized_pnl', 'USDT')}**",
        f"- Commission: **{value('commission', 'USDT')}**",
        f"- Funding: **{value('funding', 'USDT')}**",
        f"- Other income: **{value('other_income', 'USDT')}**", "",
        "Equity is wallet balance at the period end, excluding unrealized P&L. "
        "Net P&L = realized P&L + commission + funding + other income. "
        "Equity change = net income / opening wallet balance × 100. "
        "Max drawdown is the largest peak-to-trough wallet-income decline within "
        "this period; it excludes unrealized position fluctuations. Transfers in "
        "other income affect these wallet-change figures.", "",
    ]
    if metrics["equity_value"] is None:
        lines.extend([
            "Historical wallet balance could not be verified for this cutoff; "
            "equity and percentage figures are unavailable.", "",
        ])
    return lines


def _in_window(value: Any, start: datetime, end: datetime) -> bool:
    if not isinstance(value, datetime):
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return start <= value.astimezone(IST) < end


def _event_counts(
    decisions: Iterable[Mapping[str, Any]], start: datetime, end: datetime
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for decision in decisions:
        for event in decision.get("execution_events", []):
            if _in_window(event.get("timestamp"), start, end):
                status = str(event.get("status", "unknown"))
                counts[status] = counts.get(status, 0) + 1
    return counts


def _report_body(
    report_type: str,
    label: str,
    start: datetime,
    end: datetime,
    decisions: Iterable[Mapping[str, Any]],
    income_records: Iterable[Mapping[str, Any]],
    cutoff_equity: Optional[float],
    active_trades: Iterable[Mapping[str, Any]] = (),
    income_source: str = "recorded MongoDB income ledger (completeness unverified)",
) -> str:
    rows = list(decisions)
    income = [dict(row) for row in income_records]
    window_rows = [row for row in rows if _in_window(row.get("timestamp"), start, end)]
    outcomes: dict[str, int] = {}
    for row in window_rows:
        outcome = str(row.get("outcome", "unknown"))
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    events = _event_counts(rows, start, end)
    attempts = sum(outcomes.get(name, 0) for name in ("accepted", "rejected", "error"))

    closed: dict[str, list[float]] = {}
    for decision in rows:
        for event in decision.get("execution_events", []):
            if event.get("status") == "trade_closed" and _in_window(
                event.get("timestamp"), start, end
            ):
                symbol = str(decision.get("symbol") or "UNKNOWN")
                closed.setdefault(symbol, []).append(float(event.get("pnl", 0) or 0))

    lines = [
        f"# Orbit Testnet {report_type} report — {label}",
        "",
        f"> Decisions and execution events: MongoDB ledger. Account income: {income_source}.",
        "",
        f"> Reporting window: **{start.isoformat()} ≤ event time < {end.isoformat()}** "
        "(IST, end exclusive).",
        "",
        *performance_lines(report_metrics(income, cutoff_equity)),
        "## Summary",
        "",
        f"- Trade attempts: **{attempts}**",
        f"- Accepted signals: **{outcomes.get('accepted', 0)}**",
        f"- Orders submitted: **{events.get('order_submitted', 0)}**",
        f"- Orders filled: **{events.get('order_filled', 0)}**",
        f"- Strategy rejections: **{outcomes.get('rejected', 0)}**",
        f"- Risk/order rejections: **{events.get('order_rejected', 0)}**",
        f"- Errors: **{outcomes.get('error', 0)}**",
        f"- No-signal evaluations: **{outcomes.get('no_signal', 0)}**",
        f"- Closed trades: **{events.get('trade_closed', 0)}**",
        "",
        "## Closed-trade performance by asset",
        "",
        "| Asset | Closed trades | Net P&L |",
        "| :--- | ---: | ---: |",
    ]
    for symbol, values in sorted(closed.items()):
        lines.append(f"| {symbol} | {len(values)} | {sum(values):.8f} |")
    if not closed:
        lines.append("| — | 0 | 0.00000000 |")

    if report_type == "daily":
        active = list(active_trades)
        lines.extend([
            "",
            "## Unclosed decision-ledger lifecycles",
            "",
            f"- Lifecycles with a fill and no close before cutoff: **{len(active)}**",
        ])
    return "\n".join(lines) + "\n"


def build_report_body(
    report_date: date,
    decisions: Iterable[Mapping[str, Any]],
    income_records: Iterable[Mapping[str, Any]],
    active_trades: Iterable[Mapping[str, Any]] = (),
    cutoff_equity: Optional[float] = None,
    *,
    include_automation_task: bool = False,
    income_source: str = "recorded MongoDB income ledger (completeness unverified)",
) -> str:
    del include_automation_task
    start, end = report_window(report_date)
    return _report_body(
        "daily", report_date.isoformat(), start, end, decisions, income_records,
        cutoff_equity, active_trades, income_source,
    )


def build_weekly_report_body(
    week_start: date,
    decisions: Iterable[Mapping[str, Any]],
    income_records: Iterable[Mapping[str, Any]],
    cutoff_equity: Optional[float] = None,
    *,
    income_source: str = "recorded MongoDB income ledger (completeness unverified)",
) -> str:
    start, end = report_window(week_start, 7)
    label = f"{week_start.isoformat()} to {(week_start + timedelta(days=6)).isoformat()}"
    return _report_body(
        "weekly", label, start, end, decisions, income_records, cutoff_equity,
        income_source=income_source,
    )
