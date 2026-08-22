"""Net-return accounting for Binance USD-M Futures income records."""

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


@dataclass(frozen=True)
class PerformanceSummary:
    realized_pnl: float
    commission: float
    funding: float
    other_income: float
    net_pnl: float
    return_pct: float | None
    records: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PerformanceTracker:
    """Normalise and aggregate the exchange's immutable income ledger."""

    def __init__(
        self,
        futures_client: Any,
        mongo_handler: Any = None,
        execution_mode: str = "unknown",
    ) -> None:
        self.futures_client = futures_client
        self.mongo_handler = mongo_handler
        self.execution_mode = execution_mode

    @staticmethod
    def summarize(records: Iterable[dict[str, Any]], starting_equity: float | None = None) -> PerformanceSummary:
        totals: defaultdict[str, float] = defaultdict(float)
        count = 0
        for record in records:
            count += 1
            income = float(record.get("income", 0) or 0)
            income_type = str(record.get("incomeType", "OTHER")).upper()
            if income_type == "REALIZED_PNL":
                totals["realized"] += income
            elif income_type == "COMMISSION":
                totals["commission"] += income
            elif income_type == "FUNDING_FEE":
                totals["funding"] += income
            else:
                totals["other"] += income
        net = sum(totals.values())
        return_pct = None
        if starting_equity and starting_equity > 0:
            return_pct = (net / starting_equity) * 100
        return PerformanceSummary(
            realized_pnl=totals["realized"],
            commission=totals["commission"],
            funding=totals["funding"],
            other_income=totals["other"],
            net_pnl=net,
            return_pct=return_pct,
            records=count,
        )

    def sync(self, start_time_ms: int | None = None) -> PerformanceSummary:
        params = {"recvWindow": 60000}
        if start_time_ms is not None:
            params["startTime"] = start_time_ms
        records = self.futures_client.get_income_history(**params)
        if self.mongo_handler is not None:
            self.mongo_handler.store_income_records(records, self.execution_mode)
        return self.summarize(records)

    @staticmethod
    def utc_now() -> datetime:
        return datetime.now(timezone.utc)
