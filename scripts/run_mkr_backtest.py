#!/usr/bin/env python3
"""Reproduce the MKRUSDT hourly research backtest from Binance Futures data."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from orbit.strategies.mkrusdt_strategy import MKRUSDTStrategy  # noqa: E402

API_URL = "https://fapi.binance.com/fapi/v1/klines"
START = pd.Timestamp("2021-01-01T00:00:00Z")
END = pd.Timestamp("2025-09-15T01:00:00Z")


def download_candles(cache_path: Path) -> pd.DataFrame:
    """Download immutable hourly candles, caching them outside source control."""
    if cache_path.exists():
        frame = pd.read_csv(cache_path, index_col="timestamp", parse_dates=True)
        frame.index = pd.DatetimeIndex(frame.index)
        return frame

    rows: list[list[Any]] = []
    cursor = int(START.timestamp() * 1_000)
    end_ms = int(END.timestamp() * 1_000)
    while cursor < end_ms:
        response = requests.get(
            API_URL,
            params={
                "symbol": "MKRUSDT",
                "interval": "1h",
                "startTime": cursor,
                "endTime": end_ms - 1,
                "limit": 1_500,
            },
            timeout=30,
        )
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        rows.extend(batch)
        cursor = int(batch[-1][0]) + 3_600_000

    columns = [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_volume",
        "trades",
        "taker_base",
        "taker_quote",
        "unused",
    ]
    frame = pd.DataFrame(rows, columns=columns)
    frame.index = pd.to_datetime(frame.pop("timestamp"), unit="ms", utc=True)
    frame.index.name = "timestamp"
    frame = frame[["open", "high", "low", "close", "volume"]].astype(float)
    frame = frame[~frame.index.duplicated()].sort_index()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(cache_path)
    return frame


def report_segment(
    frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp
) -> dict[str, Any]:
    """Run the repository's conservative fill rules on precomputed indicators."""
    equity = 10_000.0
    peak = equity
    max_drawdown = 0.0
    pnls: list[float] = []
    values = frame[
        ["open", "high", "low", "close", "atr", "ema", "breakout_high", "breakout_low"]
    ].to_numpy()
    index = frame.index
    position = max(101, int(index.searchsorted(start)))
    while position < len(frame) - 1 and index[position] < end:
        _, _, _, close, atr, ema, high_break, low_break = values[position]
        side = 1 if close > high_break and close > ema else 0
        side = -1 if close < low_break and close < ema else side
        if not side:
            position += 1
            continue

        raw_entry = values[position + 1, 0]
        entry = raw_entry * (1 + side * 0.0002)
        stop = close - side * 1.5 * atr
        target = close + side * 6.0 * atr
        valid = stop < raw_entry < target if side == 1 else target < raw_entry < stop
        if not valid:
            position += 1
            continue

        quantity = equity * 0.01 / abs(entry - stop)
        exit_position = position + 1
        raw_exit = values[exit_position, 3]
        while exit_position < len(frame) and index[exit_position] < end:
            high, low = values[exit_position, 1], values[exit_position, 2]
            stop_hit = low <= stop if side == 1 else high >= stop
            target_hit = high >= target if side == 1 else low <= target
            if stop_hit or target_hit:
                raw_exit = stop if stop_hit else target
                break
            raw_exit = values[exit_position, 3]
            exit_position += 1
        exit_position = min(exit_position, len(frame) - 1)
        exit_price = raw_exit * (1 - side * 0.0002)
        net = (exit_price - entry) * quantity * side
        net -= (entry + exit_price) * quantity * 0.0004
        equity += net
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak)
        pnls.append(net)
        position = exit_position + 1

    wins = sum(pnl > 0 for pnl in pnls)
    gross_profit = sum(max(pnl, 0.0) for pnl in pnls)
    gross_loss = abs(sum(min(pnl, 0.0) for pnl in pnls))
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "return_pct": round((equity / 10_000 - 1) * 100, 4),
        "net_pnl": round(equity - 10_000, 2),
        "trades": len(pnls),
        "win_rate_pct": round(wins / len(pnls) * 100 if pnls else 0.0, 4),
        "profit_factor": round(gross_profit / gross_loss if gross_loss else 0.0, 4),
        "max_drawdown_pct": round(max_drawdown * 100, 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data", type=Path, default=PROJECT_ROOT / "data/MKRUSDT_1h.csv"
    )
    parser.add_argument(
        "--output", type=Path, default=PROJECT_ROOT / "results/mkr_backtest.json"
    )
    args = parser.parse_args()
    data = download_candles(args.data)
    first_split = int(len(data) * 0.60)
    second_split = int(len(data) * 0.80)
    indicator_frame = MKRUSDTStrategy(data)._indicators(data)
    boundaries = [data.index[0], data.index[first_split], data.index[second_split], END]
    segments = {
        "development": (boundaries[0], boundaries[1]),
        "validation": (boundaries[1], boundaries[2]),
        "holdout": (boundaries[2], boundaries[3]),
    }
    result = {
        "symbol": "MKRUSDT",
        "timeframe": "1h",
        "parameters": {
            "breakout_period": 24,
            "ema_period": 100,
            "atr_period": 14,
            "atr_stop_multiple": 1.5,
            "reward_risk": 4.0,
            "starting_equity": 10_000,
            "risk_per_trade_pct": 1.0,
            "fee_per_side_pct": 0.04,
            "slippage_bps_per_side": 2.0,
        },
        "segments": {
            name: report_segment(indicator_frame, start, end)
            for name, (start, end) in segments.items()
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
