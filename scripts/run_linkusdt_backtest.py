#!/usr/bin/env python3
"""Download LINKUSDT hourly candles and run the Walk-Forward Backtest."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Sequence

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from orbit.backtesting import WalkForwardBacktester  # noqa: E402
from orbit.strategies.linkusdt_strategy import LINKUSDTStrategy  # noqa: E402

SYMBOL = "LINKUSDT"
INTERVAL = "1h"
LIMIT = 1500


def download_data(output_path: Path) -> None:
    """Download ~2 years of data (Sep 2024 to Sep 2026) from Binance."""
    print("Downloading historical data...")
    start_ms = int(pd.Timestamp("2024-09-01", tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp("2026-09-07", tz="UTC").timestamp() * 1000)
    
    all_candles = []
    current_start = start_ms
    base_url = "https://fapi.binance.com/fapi/v1/klines"
    
    while current_start < end_ms:
        params = {
            "symbol": SYMBOL,
            "interval": INTERVAL,
            "startTime": current_start,
            "endTime": end_ms,
            "limit": LIMIT,
        }
        print(f"Fetching from {pd.Timestamp(current_start, unit='ms', tz='UTC')}...")
        resp = requests.get(base_url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        
        if not data:
            break
            
        all_candles.extend(data)
        current_start = data[-1][0] + 3600000  # +1 hour in ms
        time.sleep(0.5)

    print(f"Downloaded {len(all_candles)} candles")
    df = pd.DataFrame(all_candles, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_volume",
        "taker_buy_quote_volume", "ignore"
    ])
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("timestamp")
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="first")]
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path)
    print(f"Saved {len(df)} candles to {output_path}")


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
    parser.add_argument("--csv", type=Path, default=PROJECT_ROOT / "data" / "LINKUSDT_1h.csv")
    parser.add_argument("--equity", type=float, default=10_000.0)
    parser.add_argument("--risk", type=float, default=0.01)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "results" / "linkusdt_backtest.json")
    parser.add_argument("--skip-download", action="store_true", help="Skip downloading data if it exists")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    
    if not args.skip_download or not args.csv.exists():
        download_data(args.csv)
        
    print("\nRunning Backtest...")
    data = load_candles(args.csv)
    report = WalkForwardBacktester(
        lambda frame: LINKUSDTStrategy(frame, enforce_freshness=False),
        starting_equity=args.equity,
        risk_per_trade_pct=args.risk,
        fee_rate=0.0004,
        slippage_bps=2.0,
    ).run(data, symbol="LINKUSDT", warmup_bars=250)
    
    result = {
        "strategy": "LINKUSDTStrategy",
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
        "metrics": summarize(report),
    }
    
    rendered = json.dumps(result, indent=2)
    print("\n--- Results ---")
    print(rendered)
    
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(f"\nResults saved to {args.output}")
        
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
