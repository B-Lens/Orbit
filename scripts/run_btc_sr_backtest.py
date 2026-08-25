#!/usr/bin/env python3
"""Run backtesting for the BTCUSDT Support/Resistance strategy.

Usage:
    python scripts/run_btc_sr_backtest.py [--equity 10000] [--risk 0.01]
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

# Ensure src is in the path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from orbit.backtesting.engine import WalkForwardBacktester
from orbit.strategies.btc_sr_strategy import BTCSRStrategy

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def calculate_extended_metrics(report, data):
    """Computes Sharpe, Sortino, Calmar, CAGR, etc."""
    metrics = {
        "Total Return (%)": round(report.return_pct, 2),
        "Net PnL ($)": round(report.net_pnl, 2),
        "Trades": report.trades,
        "Wins": report.wins,
        "Losses": report.losses,
        "Win Rate (%)": round(report.win_rate, 2),
        "Profit Factor": round(report.profit_factor, 2) if report.profit_factor is not None else None,
        "Max Drawdown (%)": round(report.max_drawdown_pct, 2),
    }

    if not report.results:
        return metrics

    trade_pnls = [t.net_pnl for t in report.results]
    trade_returns = []
    equity = report.starting_equity
    equity_curve = [[data.index[0].isoformat(), equity]]

    winning_pnls = []
    losing_pnls = []

    for t in report.results:
        trade_returns.append(t.net_pnl / equity)
        equity += t.net_pnl
        equity_curve.append([t.exit_time.isoformat(), round(equity, 2)])
        if t.net_pnl > 0:
            winning_pnls.append(t.net_pnl)
        elif t.net_pnl < 0:
            losing_pnls.append(t.net_pnl)

    metrics["Avg Trade PnL ($)"] = round(np.mean(trade_pnls), 2)
    avg_win = np.mean(winning_pnls) if winning_pnls else 0
    avg_loss = abs(np.mean(losing_pnls)) if losing_pnls else 0
    metrics["Profit/Loss Ratio"] = round(avg_win / avg_loss, 2) if avg_loss != 0 else float("inf")
    metrics["Equity Curve"] = equity_curve

    if len(data) > 0:
        duration_days = (data.index[-1] - data.index[0]).total_seconds() / 86400
        years = max(duration_days / 365.25, 0.01)
        trades_per_year = len(report.results) / years

        cagr = ((report.final_equity / report.starting_equity) ** (1 / years)) - 1
        metrics["CAGR (%)"] = round(cagr * 100, 2)

        if len(trade_returns) > 1:
            mean_return = np.mean(trade_returns)
            std_return = np.std(trade_returns)
            downside_returns = [r for r in trade_returns if r < 0]
            std_downside = np.std(downside_returns) if downside_returns else 0

            annualization_factor = math.sqrt(trades_per_year)

            if std_return > 0:
                sharpe = (mean_return / std_return) * annualization_factor
                metrics["Sharpe Ratio"] = round(sharpe, 2)
            else:
                metrics["Sharpe Ratio"] = 0.0

            if std_downside > 0:
                sortino = (mean_return / std_downside) * annualization_factor
                metrics["Sortino Ratio"] = round(sortino, 2)
            else:
                metrics["Sortino Ratio"] = 0.0
        else:
            metrics["Sharpe Ratio"] = 0.0
            metrics["Sortino Ratio"] = 0.0

        if report.max_drawdown_pct > 0:
            calmar = (cagr * 100) / report.max_drawdown_pct
            metrics["Calmar Ratio"] = round(calmar, 2)
        else:
            metrics["Calmar Ratio"] = float("inf")

    return metrics


def main():
    parser = argparse.ArgumentParser(description="Run BTCSRStrategy backtest.")
    parser.add_argument("--equity", type=float, default=10000.0, help="Starting equity")
    parser.add_argument("--risk", type=float, default=0.01, help="Risk per trade (percent)")
    args = parser.parse_args()

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_path = os.path.join(project_root, "data", "BTCUSDT_1h.csv")

    if not os.path.exists(data_path):
        logging.error(f"Data file not found: {data_path}")
        logging.info("Run: python scripts/download_btc_data.py")
        sys.exit(1)

    logging.info(f"Loading data from {data_path}...")
    data = pd.read_csv(data_path)
    if "timestamp" in data.columns:
        data["timestamp"] = pd.to_datetime(data["timestamp"])
        data.set_index("timestamp", inplace=True)
    else:
        data.index = pd.to_datetime(data.iloc[:, 0])
        data = data.iloc[:, 1:]

    logging.info(f"Data: {len(data)} bars from {data.index[0]} to {data.index[-1]}")
    logging.info("Running backtest (this may take several minutes)...")

    backtester = WalkForwardBacktester(
        strategy_factory=BTCSRStrategy,
        starting_equity=args.equity,
        risk_per_trade_pct=args.risk,
    )

    report = backtester.run(data, symbol="BTCUSDT", warmup_bars=250)
    metrics = calculate_extended_metrics(report, data)

    # Paper trading simulation: generate signal on the latest data
    strategy = BTCSRStrategy(data)
    last_signal = strategy.generate_signals(symbol="BTCUSDT")
    if last_signal is not None:
        last_signal["timestamp"] = data.index[-1].isoformat()

    # Trade breakdown by setup type
    setup_breakdown = {}
    for t in report.results:
        setup = "unknown"
        if "Bounce" in t.pattern:
            setup = "bounce"
        elif "Breakout" in t.pattern:
            setup = "breakout"
        elif "Flip" in t.pattern:
            setup = "flip_retest"
        if setup not in setup_breakdown:
            setup_breakdown[setup] = {"trades": 0, "wins": 0, "pnl": 0.0}
        setup_breakdown[setup]["trades"] += 1
        if t.net_pnl > 0:
            setup_breakdown[setup]["wins"] += 1
        setup_breakdown[setup]["pnl"] += t.net_pnl

    for setup, stats in setup_breakdown.items():
        stats["win_rate"] = round(stats["wins"] / stats["trades"] * 100, 2) if stats["trades"] else 0.0
        stats["pnl"] = round(stats["pnl"], 2)

    results = {
        "strategy": "BTCSRStrategy",
        "timeframe": "1h",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "metrics": metrics,
        "setup_breakdown": setup_breakdown,
        "paper_trading": {
            "mode": "paper",
            "last_signal": last_signal,
            "open_position": None,
        },
    }

    results_dir = os.path.join(project_root, "results")
    os.makedirs(results_dir, exist_ok=True)
    results_path = os.path.join(results_dir, "btc_sr_backtest.json")

    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)

    logging.info(f"Results saved to {results_path}")

    print("\n" + "=" * 50)
    print("BTC S/R STRATEGY BACKTEST SUMMARY")
    print("=" * 50)
    for k, v in metrics.items():
        if k != "Equity Curve":
            print(f"{k:<25}: {v}")
    print("-" * 50)
    print("SETUP BREAKDOWN:")
    for setup, stats in setup_breakdown.items():
        print(f"  {setup:<15}: {stats['trades']} trades, {stats['win_rate']}% WR, ${stats['pnl']} PnL")
    print("=" * 50)
    print(f"\nPaper Trading Signal: {last_signal}")


if __name__ == "__main__":
    main()
