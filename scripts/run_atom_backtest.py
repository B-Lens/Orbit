#!/usr/bin/env python3
"""Run the ATOMUSDT 15-minute VWAP+EMA intraday backtest.

Usage
-----
    poetry run python scripts/run_atom_backtest.py [--equity 10000] [--risk 0.01]

The script:
  1. Generates 6 months of synthetic 15-minute OHLCV data that reproduces
     typical ATOM intraday characteristics (realistic price swings, daily VWAP
     drift, volume spikes at momentum inflection points).
  2. Runs a walk-forward backtest using the production WalkForwardBacktester.
  3. Computes extended performance metrics (Sharpe, Sortino, Calmar, CAGR).
  4. Saves a JSON report to results/atom_backtest.json.
  5. Prints a summary table.

If you have real ATOMUSDT 15-minute data saved as data/ATOMUSDT_15m.csv
(columns: timestamp, open, high, low, close, volume), pass --data-file to use
that instead of the synthetic dataset.
"""

import argparse
import datetime
import json
import logging
import math
import os
import sys

import numpy as np
import pandas as pd

# Ensure src is importable when run from the project root
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from orbit.backtesting.engine import WalkForwardBacktester  # noqa: E402
from orbit.strategies.atomusdt_strategy import ATOMUSDTStrategy  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Synthetic data generator
# ---------------------------------------------------------------------------

def generate_atom_synthetic_data(
    n_bars: int = 17_280,  # ~6 months of 15-minute bars
    start: str = "2026-03-01",
    seed: int = 42,
) -> pd.DataFrame:
    """Return a realistic synthetic ATOMUSDT 15-minute OHLCV DataFrame.

    The price path is a geometric Brownian motion with:
    - A daily drift that alternates between bull and bear regimes.
    - Intraday volatility that peaks at the open/close of major sessions.
    - Volume spikes correlated with price momentum breaks.
    """
    rng = np.random.default_rng(seed)
    index = pd.date_range(start, periods=n_bars, freq="15min")

    # --- Price path ---
    start_price = 8.50  # approximate ATOM price at start of window
    bar_vol = 0.0025    # ~0.25% per 15-min bar ≈ ~2.4% daily

    # Regime: each day has a slight directional drift
    days = (n_bars * 15) // (60 * 24) + 1
    daily_drift = rng.choice([-1, 0, 1], size=days, p=[0.35, 0.30, 0.35]) * 0.0008

    drifts = np.repeat(daily_drift, 96)[:n_bars]
    shocks = rng.normal(0, bar_vol, n_bars)
    log_returns = drifts + shocks
    closes = start_price * np.exp(np.cumsum(log_returns))

    # --- OHLC construction ---
    intrabar_range = np.abs(rng.normal(0, bar_vol * 0.8, n_bars))
    highs = closes * (1 + intrabar_range)
    lows = closes * (1 - intrabar_range)
    opens = np.roll(closes, 1)
    opens[0] = start_price

    # --- Volume: base + session spikes + momentum spikes ---
    base_vol = rng.uniform(50_000, 150_000, n_bars)

    # Hour of day spikes (UTC): 8-10h, 13-15h (EU/US overlaps)
    hour_of_day = (index.hour * 60 + index.minute) / 60
    session_mult = 1.0 + 0.8 * np.exp(-((hour_of_day - 9) ** 2) / 4) + \
                   0.6 * np.exp(-((hour_of_day - 14) ** 2) / 4)

    # Momentum spike: high |return| → higher volume
    mom_spike = 1.0 + 2.5 * (np.abs(log_returns) > bar_vol * 1.8)
    volume = base_vol * session_mult * mom_spike

    df = pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volume,
        },
        index=index,
    )
    # Ensure OHLC consistency
    df["high"] = df[["open", "high", "close"]].max(axis=1)
    df["low"] = df[["open", "low", "close"]].min(axis=1)
    return df


# ---------------------------------------------------------------------------
# Extended metrics
# ---------------------------------------------------------------------------

def calculate_extended_metrics(report, data: pd.DataFrame) -> dict:
    """Compute Sharpe, Sortino, Calmar, CAGR and other performance metrics."""
    metrics: dict = {
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
        return metrics

    trade_pnls = [t.net_pnl for t in report.results]
    winning_pnls = [p for p in trade_pnls if p > 0]
    losing_pnls = [p for p in trade_pnls if p <= 0]

    equity = report.starting_equity
    equity_curve = [[data.index[0].isoformat(), equity]]
    trade_returns = []
    for t in report.results:
        trade_returns.append(t.net_pnl / equity)
        equity += t.net_pnl
        equity_curve.append([t.exit_time.isoformat(), round(equity, 4)])

    metrics["Avg Trade PnL ($)"] = round(float(np.mean(trade_pnls)), 4)
    avg_win = float(np.mean(winning_pnls)) if winning_pnls else 0.0
    avg_loss = abs(float(np.mean(losing_pnls))) if losing_pnls else 0.0
    metrics["Avg Win ($)"] = round(avg_win, 4)
    metrics["Avg Loss ($)"] = round(avg_loss, 4)
    metrics["Profit/Loss Ratio"] = round(avg_win / avg_loss, 3) if avg_loss else float("inf")
    metrics["Equity Curve"] = equity_curve

    duration_days = (data.index[-1] - data.index[0]).total_seconds() / 86400
    years = max(duration_days / 365.25, 0.01)
    trades_per_year = len(report.results) / years

    cagr = ((report.final_equity / report.starting_equity) ** (1 / years)) - 1
    metrics["CAGR (%)"] = round(cagr * 100, 2)

    if len(trade_returns) > 1:
        arr = np.array(trade_returns)
        mean_r = float(np.mean(arr))
        std_r = float(np.std(arr))
        downside = arr[arr < 0]
        std_down = float(np.std(downside)) if len(downside) > 0 else 0.0
        ann_factor = math.sqrt(trades_per_year)

        metrics["Sharpe Ratio"] = round((mean_r / std_r) * ann_factor, 3) if std_r > 0 else 0.0
        metrics["Sortino Ratio"] = (
            round((mean_r / std_down) * ann_factor, 3) if std_down > 0 else 0.0
        )
    else:
        metrics["Sharpe Ratio"] = 0.0
        metrics["Sortino Ratio"] = 0.0

    if report.max_drawdown_pct > 0:
        metrics["Calmar Ratio"] = round((cagr * 100) / report.max_drawdown_pct, 3)
    else:
        metrics["Calmar Ratio"] = float("inf")

    return metrics


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Run ATOMUSDTStrategy backtest.")
    parser.add_argument("--equity", type=float, default=10_000.0, help="Starting equity in USDT")
    parser.add_argument("--risk", type=float, default=0.01, help="Risk per trade (fraction)")
    parser.add_argument("--data-file", type=str, default="", help="Path to real ATOMUSDT_15m.csv")
    args = parser.parse_args()

    # ---- Load data --------------------------------------------------------
    if args.data_file and os.path.exists(args.data_file):
        logger.info("Loading real data from %s", args.data_file)
        data = pd.read_csv(args.data_file)
        ts_col = "timestamp" if "timestamp" in data.columns else data.columns[0]
        data[ts_col] = pd.to_datetime(data[ts_col])
        data.set_index(ts_col, inplace=True)
        data.index.name = "timestamp"
    else:
        logger.info("No real data file provided – using synthetic ATOMUSDT dataset.")
        data = generate_atom_synthetic_data()

    logger.info("Dataset: %d bars  [%s → %s]", len(data), data.index[0], data.index[-1])

    # ---- Backtest ---------------------------------------------------------
    backtester = WalkForwardBacktester(
        strategy_factory=ATOMUSDTStrategy,
        starting_equity=args.equity,
        risk_per_trade_pct=args.risk,
        fee_rate=0.0004,   # Binance futures maker/taker blended
        slippage_bps=2.0,  # 2 bps per side
    )
    report = backtester.run(data, symbol="ATOMUSDT", warmup_bars=100)

    # ---- Metrics ----------------------------------------------------------
    metrics = calculate_extended_metrics(report, data)

    # ---- Latest signal (paper-trading preview) ----------------------------
    strategy = ATOMUSDTStrategy(data)
    last_signal = strategy.generate_signals(symbol="ATOMUSDT")
    if last_signal:
        last_signal["timestamp"] = data.index[-1].isoformat()

    # ---- Persist results --------------------------------------------------
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    results_dir = os.path.join(project_root, "results")
    os.makedirs(results_dir, exist_ok=True)
    results_path = os.path.join(results_dir, "atom_backtest.json")

    output = {
        "strategy": "ATOMUSDTStrategy",
        "timeframe": "15m",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "data_source": args.data_file if args.data_file else "synthetic",
        "metrics": {k: v for k, v in metrics.items() if k != "Equity Curve"},
        "equity_curve": metrics.get("Equity Curve", []),
        "paper_trading": {
            "mode": "paper",
            "last_signal": last_signal,
            "open_position": None,
        },
    }
    with open(results_path, "w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2)
    logger.info("Results saved to %s", results_path)

    # ---- Print summary ---------------------------------------------------
    print("\n" + "=" * 50)
    print("ATOMUSDT 15m VWAP+EMA BACKTEST SUMMARY")
    print("=" * 50)
    for key, val in metrics.items():
        if key != "Equity Curve":
            print(f"  {key:<25}: {val}")
    print("=" * 50)
    print(f"\nPaper-trading last signal: {last_signal}")


if __name__ == "__main__":
    main()
