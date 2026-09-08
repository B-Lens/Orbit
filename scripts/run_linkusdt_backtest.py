#!/usr/bin/env python3
"""Backtest the rejected LINKUSDT research candidate on hourly CSV candles."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from orbit.backtesting import WalkForwardBacktester  # noqa: E402
from orbit.strategies.linkusdt_strategy import LINKUSDTResearchStrategy  # noqa: E402


def load_candles(path: Path) -> pd.DataFrame:
    """Load unique, chronological hourly OHLCV candles."""
    data = pd.read_csv(path, parse_dates=["timestamp"], index_col="timestamp")
    required = ["open", "high", "low", "close", "volume"]
    missing = set(required) - set(data.columns)
    if missing:
        raise ValueError("Missing columns: " + ", ".join(sorted(missing)))
    data = data[required].astype(float)
    if data.index.has_duplicates or not data.index.is_monotonic_increasing:
        raise ValueError("Timestamps must be unique and sorted oldest to newest")
    return data


def metrics(report: object) -> dict[str, float | int | None]:
    """Render the stable headline metrics used by the research report."""
    return {
        "starting_equity": round(report.starting_equity, 2),  # type: ignore[attr-defined]
        "final_equity": round(report.final_equity, 2),  # type: ignore[attr-defined]
        "net_pnl": round(report.net_pnl, 2),  # type: ignore[attr-defined]
        "return_pct": round(report.return_pct, 2),  # type: ignore[attr-defined]
        "trades": report.trades,  # type: ignore[attr-defined]
        "wins": report.wins,  # type: ignore[attr-defined]
        "losses": report.losses,  # type: ignore[attr-defined]
        "win_rate": round(report.win_rate, 2),  # type: ignore[attr-defined]
        "profit_factor": (
            round(report.profit_factor, 2)  # type: ignore[attr-defined]
            if report.profit_factor is not None  # type: ignore[attr-defined]
            else None
        ),
        "max_drawdown_pct": round(report.max_drawdown_pct, 2),  # type: ignore[attr-defined]
    }


def run(data: pd.DataFrame, equity: float, risk: float) -> dict[str, object]:
    """Run one chronological segment with the documented cost assumptions."""
    report = WalkForwardBacktester(
        LINKUSDTResearchStrategy,
        starting_equity=equity,
        risk_per_trade_pct=risk,
        fee_rate=0.0004,
        slippage_bps=2.0,
    ).run(data, symbol="LINKUSDT", warmup_bars=250)
    return metrics(report)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="hourly CSV with a timestamp column")
    parser.add_argument("--equity", type=float, default=10_000.0)
    parser.add_argument("--risk", type=float, default=0.01)
    parser.add_argument("--output", type=Path, help="optional JSON result path")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    data = load_candles(args.csv)
    split = len(data) // 2
    result = {
        "strategy": "LINKUSDTResearchStrategy",
        "status": "rejected_not_registered",
        "symbol": "LINKUSDT",
        "timeframe": "1h",
        "data_start": data.index[0].isoformat(),
        "data_end": data.index[-1].isoformat(),
        "assumptions": {
            "risk_per_trade_pct": args.risk * 100,
            "fee_rate_per_side_pct": 0.04,
            "slippage_bps_per_side": 2.0,
            "same_bar_stop_target": "stop_first",
        },
        "metrics": {
            "full_sample": run(data, args.equity, args.risk),
            "first_half": run(data.iloc[:split], args.equity, args.risk),
            "second_half": run(data.iloc[split:], args.equity, args.risk),
        },
    }
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
