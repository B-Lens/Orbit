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
        after = int(tracker.utc_now().timestamp() * 1000)
        subsequent = tracker.sync_window(int(end.timestamp() * 1000), after + 1)
        if not any(before <= int(row.get("time", 0) or 0) <= after for row in tracker.last_records):
            return float(account["totalWalletBalance"]) - subsequent.net_pnl
    raise RuntimeError("Account income changed during every snapshot; refusing to publish inconsistent equity")


def performance_lines(metrics: Mapping[str, Any]) -> list[str]:
    def value(key: str, unit: str) -> str:
        number = metrics[key]
        return "Unavailable" if number is None else f"{number:.8f} {unit}"

    return [
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
