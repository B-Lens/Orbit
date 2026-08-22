#!/usr/bin/env python3
"""Run the walk-forward backtest for BCHUSDTStrategy.

Fetches 4-hour BCH/USDT OHLCV data from Binance public API (no key needed),
runs the walk-forward backtester, saves results to ``results/bch_backtest.json``,
and prints a human-readable summary.

Usage
-----
::

    poetry run python scripts/run_bch_backtest.py [--equity 10000] [--risk 0.01]

Options
~~~~~~~
--equity FLOAT    Starting equity in USDT (default 10 000)
--risk   FLOAT    Fraction of equity risked per trade (default 0.01)
--days   INT      How many days of 4-hour history to fetch (default 365)
--output PATH     Override the output JSON path
"""

import argparse
import datetime
import json
import logging
import math
import os
import sys
import time

import numpy as np
import pandas as pd
import urllib.request

# Ensure src/ is importable when run directly.
sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from orbit.backtesting.engine import WalkForwardBacktester
from orbit.strategies.bch_strategy import BCHUSDTStrategy

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
log = logging.getLogger(__name__)


# ── data helpers ──────────────────────────────────────────────────────────────


def _fetch_binance_klines(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    max_per_request: int = 1000,
) -> list[list]:
    """Fetch OHLCV klines from Binance REST API (no authentication needed)."""
    base = "https://api.binance.com/api/v3/klines"
    rows: list[list] = []
    current_start = start_ms
    while current_start < end_ms:
        url = (
            f"{base}?symbol={symbol}&interval={interval}"
            f"&startTime={current_start}&endTime={end_ms}"
            f"&limit={max_per_request}"
        )
        log.info("Fetching %s %s from %s …", symbol, interval, url[:80])
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                batch = json.loads(resp.read())
        except Exception as exc:  # noqa: BLE001
            log.error("Binance API error: %s – using synthetic data instead.", exc)
            return []
        if not batch:
            break
        rows.extend(batch)
        current_start = int(batch[-1][0]) + 1
        if len(batch) < max_per_request:
            break
        time.sleep(0.2)
    return rows


def _klines_to_df(klines: list[list]) -> pd.DataFrame:
    df = pd.DataFrame(
        klines,
        columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_vol", "trades", "taker_base", "taker_quote", "_",
        ],
    )
    df["timestamp"] = pd.to_datetime(df["open_time"].astype(int), unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    df.sort_index(inplace=True)
    return df[["open", "high", "low", "close", "volume"]]


def _synthetic_data(n: int = 1500) -> pd.DataFrame:
    """Generate pseudo-realistic BCH-like price data for offline testing."""
    rng = np.random.default_rng(42)
    index = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")

    # Simulate multi-regime price: uptrend → consolidation → downtrend → recovery
    log_returns = np.concatenate(
        [
            rng.normal(0.0006, 0.012, n // 4),   # uptrend
            rng.normal(0.0000, 0.009, n // 4),   # consolidation
            rng.normal(-0.0005, 0.014, n // 4),  # downtrend
            rng.normal(0.0004, 0.011, n - 3 * (n // 4)),  # recovery
        ]
    )
    close = 320.0 * np.exp(np.cumsum(log_returns))
    high = close * (1 + np.abs(rng.normal(0.003, 0.002, n)))
    low = close * (1 - np.abs(rng.normal(0.003, 0.002, n)))
    open_ = np.roll(close, 1)
    open_[0] = close[0]

    base_vol = rng.uniform(3_000, 12_000, n)
    # Realistic volume spikes correlate with large candles
    candle_size = np.abs(np.log(close / open_))
    volume = base_vol * (1 + 3 * candle_size / candle_size.max())

    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )


def load_data(symbol: str, interval: str, days: int) -> pd.DataFrame:
    """Try live Binance data; fall back to synthetic data."""
    end_ms = int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)
    start_ms = end_ms - days * 24 * 3_600 * 1_000

    klines = _fetch_binance_klines(symbol, interval, start_ms, end_ms)
    if klines:
        log.info("Fetched %d bars from Binance.", len(klines))
        return _klines_to_df(klines)

    log.warning("No live data – generating synthetic BCH data for demonstration.")
    n_bars = days * 6  # 6 × 4-hour bars per day
    return _synthetic_data(n_bars)


# ── metrics ───────────────────────────────────────────────────────────────────


def extended_metrics(report, data: pd.DataFrame) -> dict:
    """Compute Sharpe, Sortino, Calmar, CAGR and per-trade statistics."""
    m: dict = {
        "Total Return (%)": round(report.return_pct, 2),
        "Net PnL ($)": round(report.net_pnl, 2),
        "Trades": report.trades,
        "Wins": report.wins,
        "Losses": report.losses,
        "Win Rate (%)": round(report.win_rate, 2),
        "Profit Factor": round(report.profit_factor, 2) if report.profit_factor else None,
        "Max Drawdown (%)": round(report.max_drawdown_pct, 2),
    }

    if not report.results:
        return m

    trade_pnls = [t.net_pnl for t in report.results]
    equity = report.starting_equity
    equity_curve = [[data.index[0].isoformat(), equity]]
    winning, losing = [], []

    trade_returns = []
    for t in report.results:
        trade_returns.append(t.net_pnl / equity)
        equity += t.net_pnl
        equity_curve.append([t.exit_time.isoformat(), round(equity, 2)])
        (winning if t.net_pnl > 0 else losing).append(t.net_pnl)

    m["Avg Trade PnL ($)"] = round(float(np.mean(trade_pnls)), 2)
    avg_win = float(np.mean(winning)) if winning else 0.0
    avg_loss = abs(float(np.mean(losing))) if losing else 0.0
    m["Avg Win ($)"] = round(avg_win, 2)
    m["Avg Loss ($)"] = round(avg_loss, 2)
    m["Profit/Loss Ratio"] = round(avg_win / avg_loss, 2) if avg_loss else float("inf")
    m["Equity Curve"] = equity_curve

    if len(data) > 0:
        duration_days = (data.index[-1] - data.index[0]).total_seconds() / 86_400
        years = max(duration_days / 365.25, 0.01)
        trades_per_year = len(report.results) / years
        cagr = (report.final_equity / report.starting_equity) ** (1 / years) - 1
        m["CAGR (%)"] = round(cagr * 100, 2)

        if len(trade_returns) > 1:
            mean_r = float(np.mean(trade_returns))
            std_r = float(np.std(trade_returns))
            downside = [r for r in trade_returns if r < 0]
            std_down = float(np.std(downside)) if downside else 0.0
            ann = math.sqrt(trades_per_year)

            m["Sharpe Ratio"] = round((mean_r / std_r) * ann, 2) if std_r > 0 else 0.0
            m["Sortino Ratio"] = round((mean_r / std_down) * ann, 2) if std_down > 0 else 0.0
        else:
            m["Sharpe Ratio"] = 0.0
            m["Sortino Ratio"] = 0.0

        if report.max_drawdown_pct > 0:
            m["Calmar Ratio"] = round((cagr * 100) / report.max_drawdown_pct, 2)
        else:
            m["Calmar Ratio"] = float("inf")

    return m


# ── main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Run BCHUSDTStrategy backtest.")
    parser.add_argument("--equity", type=float, default=10_000.0)
    parser.add_argument("--risk", type=float, default=0.01)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--output", type=str, default="")
    args = parser.parse_args()

    log.info("Loading BCH/USDT 4-hour data (%d days)…", args.days)
    data = load_data("BCHUSDT", "4h", args.days)
    log.info("Loaded %d 4-hour bars (%s → %s).", len(data), data.index[0], data.index[-1])

    log.info("Running walk-forward backtest…")
    backtester = WalkForwardBacktester(
        strategy_factory=BCHUSDTStrategy,
        starting_equity=args.equity,
        risk_per_trade_pct=args.risk,
    )
    report = backtester.run(data, symbol="BCHUSDT", warmup_bars=210)

    metrics = extended_metrics(report, data)

    # Latest signal on full dataset
    strategy = BCHUSDTStrategy(data)
    last_signal = strategy.generate_signals(symbol="BCHUSDT")
    if last_signal:
        last_signal["timestamp"] = data.index[-1].isoformat()

    output = {
        "strategy": "BCHUSDTStrategy",
        "timeframe": "4h",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "data_range": {
            "start": data.index[0].isoformat(),
            "end": data.index[-1].isoformat(),
            "bars": len(data),
        },
        "metrics": metrics,
        "paper_trading": {
            "mode": "paper",
            "last_signal": last_signal,
            "open_position": None,
        },
    }

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    results_dir = os.path.join(project_root, "results")
    os.makedirs(results_dir, exist_ok=True)
    out_path = args.output or os.path.join(results_dir, "bch_backtest.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2, default=str)
    log.info("Results saved to %s", out_path)

    print("\n" + "=" * 50)
    print("BCH/USDT STRATEGY – BACKTEST SUMMARY")
    print("=" * 50)
    for k, v in metrics.items():
        if k not in ("Equity Curve",):
            print(f"{k:<25}: {v}")
    print("=" * 50)
    print("\nPaper trading last signal:", last_signal)


if __name__ == "__main__":
    main()
