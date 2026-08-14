"""Conservative walk-forward backtester for the production signal contract."""

from dataclasses import asdict, dataclass
from typing import Any, Callable

import pandas as pd


@dataclass(frozen=True)
class TradeResult:
    side: str
    entry_time: Any
    exit_time: Any
    entry_price: float
    exit_price: float
    quantity: float
    gross_pnl: float
    costs: float
    net_pnl: float
    outcome: str
    pattern: str


@dataclass(frozen=True)
class BacktestReport:
    starting_equity: float
    final_equity: float
    net_pnl: float
    return_pct: float
    trades: int
    wins: int
    losses: int
    win_rate: float
    profit_factor: float | None
    max_drawdown_pct: float
    results: tuple[TradeResult, ...]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["results"] = [asdict(item) for item in self.results]
        return result


class WalkForwardBacktester:
    """Evaluate a strategy on prefixes only, preventing future-data access.

    A single position is allowed at a time. If stop and target are touched in
    the same candle, the stop is chosen as the conservative outcome.
    """

    def __init__(
        self,
        strategy_factory: Callable[[pd.DataFrame], Any],
        *,
        starting_equity: float = 10_000.0,
        risk_per_trade_pct: float = 0.01,
        fee_rate: float = 0.0004,
        slippage_bps: float = 2.0,
    ) -> None:
        if starting_equity <= 0 or not 0 < risk_per_trade_pct <= 1:
            raise ValueError("Invalid equity or risk percentage")
        self.strategy_factory = strategy_factory
        self.starting_equity = starting_equity
        self.risk_per_trade_pct = risk_per_trade_pct
        self.fee_rate = fee_rate
        self.slippage = slippage_bps / 10_000

    def run(self, data: pd.DataFrame, *, symbol: str, warmup_bars: int = 250) -> BacktestReport:
        required = {"open", "high", "low", "close", "volume"}
        if not required.issubset(data.columns):
            raise ValueError(f"Data must contain {sorted(required)}")
        if not data.index.is_monotonic_increasing:
            raise ValueError("Data index must be sorted oldest to newest")

        equity = self.starting_equity
        peak = equity
        max_drawdown = 0.0
        results: list[TradeResult] = []
        index = max(warmup_bars, 1)

        while index < len(data) - 1:
            strategy = self.strategy_factory(data.iloc[: index + 1].copy())
            signal = strategy.generate_signals(symbol=symbol)
            if not signal or signal.get("signal") not in ("BUY", "SELL"):
                index += 1
                continue

            side = signal["signal"]
            raw_entry = float(signal.get("entry_price") or data["close"].iloc[index])
            stop = float(signal["stop_loss"])
            target = float(signal["take_profit"])
            if (side == "BUY" and not stop < raw_entry < target) or (
                side == "SELL" and not target < raw_entry < stop
            ):
                index += 1
                continue

            entry = raw_entry * (1 + self.slippage if side == "BUY" else 1 - self.slippage)
            risk_per_unit = abs(entry - stop)
            quantity = (equity * self.risk_per_trade_pct) / risk_per_unit
            exit_index, raw_exit, outcome = self._resolve_exit(
                data, index + 1, side, stop, target
            )
            exit_price = raw_exit * (
                1 - self.slippage if side == "BUY" else 1 + self.slippage
            )
            direction = 1 if side == "BUY" else -1
            gross = (exit_price - entry) * quantity * direction
            costs = (entry + exit_price) * quantity * self.fee_rate
            net = gross - costs
            equity += net
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
            results.append(TradeResult(
                side=side, entry_time=data.index[index], exit_time=data.index[exit_index],
                entry_price=entry, exit_price=exit_price, quantity=quantity,
                gross_pnl=gross, costs=costs, net_pnl=net, outcome=outcome,
                pattern=str(signal.get("pattern", "unknown")),
            ))
            index = exit_index + 1

        wins = sum(result.net_pnl > 0 for result in results)
        losses = sum(result.net_pnl <= 0 for result in results)
        gross_profit = sum(max(result.net_pnl, 0) for result in results)
        gross_loss = abs(sum(min(result.net_pnl, 0) for result in results))
        profit_factor = gross_profit / gross_loss if gross_loss else None
        return BacktestReport(
            starting_equity=self.starting_equity,
            final_equity=equity,
            net_pnl=equity - self.starting_equity,
            return_pct=((equity / self.starting_equity) - 1) * 100,
            trades=len(results), wins=wins, losses=losses,
            win_rate=(wins / len(results) * 100) if results else 0.0,
            profit_factor=profit_factor, max_drawdown_pct=max_drawdown * 100,
            results=tuple(results),
        )

    @staticmethod
    def _resolve_exit(
        data: pd.DataFrame, start: int, side: str, stop: float, target: float
    ) -> tuple[int, float, str]:
        for position in range(start, len(data)):
            low = float(data["low"].iloc[position])
            high = float(data["high"].iloc[position])
            stop_hit = low <= stop if side == "BUY" else high >= stop
            target_hit = high >= target if side == "BUY" else low <= target
            if stop_hit:
                return position, stop, "stop"
            if target_hit:
                return position, target, "target"
        return len(data) - 1, float(data["close"].iloc[-1]), "end_of_data"

