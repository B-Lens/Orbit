#!/usr/bin/env python3
"""Backtest SOLUSDT hourly candles with conservative execution assumptions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from orbit.backtesting import WalkForwardBacktester  # noqa: E402
from orbit.strategies.solusdt_strategy import SOLUSDTStrategy  # noqa: E402


def load_candles(path: Path) -> pd.DataFrame:
    """Load and validate an oldest-to-newest OHLCV CSV."""
    data = pd.read_csv(path, parse_dates=["timestamp"], index_col="timestamp")
    required = ["open", "high", "low", "close", "volume"]
    missing = set(required) - set(data.columns)
    if missing:
        raise ValueError("Missing columns: " + ", ".join(sorted(missing)))
    data = data[required].astype(float)
    if data.index.has_duplicates or not data.index.is_monotonic_increasing:
        raise ValueError("Timestamps must be unique and sorted oldest to newest")
    return data


def summarize(report: Any) -> dict[str, Any]:
    """Return stable, JSON-serializable headline metrics."""
    return {
        "starting_equity": round(report.starting_equity, 2),
        "final_equity": round(report.final_equity, 2),
        "net_pnl": round(report.net_pnl, 2),
        "return_pct": round(report.return_pct, 2),
        "trades": report.trades,
        "wins": report.wins,
        "losses": report.losses,
        "win_rate": round(report.win_rate, 2),
        "profit_factor": (
            round(report.profit_factor, 2) if report.profit_factor is not None else None
        ),
        "max_drawdown_pct": round(report.max_drawdown_pct, 2),
    }


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
    report = WalkForwardBacktester(
        SOLUSDTStrategy,
        starting_equity=args.equity,
        risk_per_trade_pct=args.risk,
        fee_rate=0.0004,
        slippage_bps=2.0,
    ).run(data, symbol="SOLUSDT", warmup_bars=200)
    result = {
        "strategy": "SOLUSDTStrategy",
        "symbol": "SOLUSDT",
        "timeframe": "1h",
        "data_start": data.index[0].isoformat(),
        "data_end": data.index[-1].isoformat(),
        "assumptions": {
            "risk_per_trade_pct": args.risk * 100,
            "fee_rate_per_side_pct": 0.04,
            "slippage_bps_per_side": 2.0,
            "same_bar_stop_target": "stop_first",
        },
        "metrics": summarize(report),
    }
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
