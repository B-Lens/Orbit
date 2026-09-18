"""
Quantitative Backtesting & Performance Analysis Suite for Ethereum Strategies in Orbit.
Runs walk-forward backtests across 15m and 1h datasets, computes Sharpe, Sortino, Calmar,
Max Drawdown, Profit Factor, and produces visualization charts.
"""

import os
import json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from orbit.backtesting import WalkForwardBacktester
from orbit.strategies.eth_strategies import (
    AggloReversalETH,
    AdaptiveSuperTrendRegimeETH,
    MultiConfluenceMeanReversionETH,
    EMATrendBreakoutETH,
    SMCLiquiditySweepETH,
    HMAMACDMomentumETH,
)


def calculate_advanced_metrics(report, data: pd.DataFrame, freq: str = "15m"):
    """Calculate Sharpe, Sortino, Calmar, Expectancy, and Drawdown metrics."""
    if not report.results or len(report.results) == 0:
        return {
            "Total Return (%)": 0.0,
            "Net PnL ($)": 0.0,
            "Trades": 0,
            "Wins": 0,
            "Losses": 0,
            "Win Rate (%)": 0.0,
            "Profit Factor": 0.0,
            "Sharpe Ratio": 0.0,
            "Sortino Ratio": 0.0,
            "Calmar Ratio": 0.0,
            "Max Drawdown (%)": 0.0,
            "Avg Trade PnL ($)": 0.0,
            "Profit/Loss Ratio": 0.0,
            "CAGR (%)": 0.0,
            "Equity Curve": [],
        }

    trades_pnl = np.array([r.net_pnl for r in report.results])
    wins = trades_pnl[trades_pnl > 0]
    losses = trades_pnl[trades_pnl <= 0]
    
    avg_win = float(np.mean(wins)) if len(wins) > 0 else 0.0
    avg_loss = float(abs(np.mean(losses))) if len(losses) > 0 else 0.0
    pl_ratio = avg_win / avg_loss if avg_loss > 0 else avg_win

    start_dt = data.index[0]
    end_dt = data.index[-1]
    days = max((end_dt - start_dt).total_seconds() / 86400, 1.0)
    years = days / 365.25

    timestamps = [start_dt] + [r.exit_time for r in report.results]
    equities = [report.starting_equity]
    curr_eq = report.starting_equity
    for r in report.results:
        curr_eq += r.net_pnl
        equities.append(curr_eq)

    eq_series = pd.Series(equities, index=pd.to_datetime(timestamps))
    daily_equity = eq_series.resample("D").last().ffill()
    daily_returns = daily_equity.pct_change().dropna()

    rf_daily = 0.03 / 365.25
    excess_returns = daily_returns - rf_daily
    
    if len(daily_returns) > 1 and daily_returns.std() > 0:
        sharpe = float((excess_returns.mean() / daily_returns.std()) * np.sqrt(365.25))
    else:
        sharpe = 0.0

    downside_returns = daily_returns[daily_returns < 0]
    if len(downside_returns) > 1 and downside_returns.std() > 0:
        sortino = float((excess_returns.mean() / downside_returns.std()) * np.sqrt(365.25))
    else:
        sortino = 0.0

    total_ret = report.final_equity / report.starting_equity
    cagr = float((total_ret ** (1.0 / years) - 1) * 100) if years > 0 and total_ret > 0 else 0.0

    mdd = report.max_drawdown_pct
    calmar = cagr / mdd if mdd > 0 else 0.0
    profit_factor = report.profit_factor if report.profit_factor is not None else 0.0

    return {
        "Total Return (%)": round(report.return_pct, 2),
        "Net PnL ($)": round(report.net_pnl, 2),
        "Trades": report.trades,
        "Wins": report.wins,
        "Losses": report.losses,
        "Win Rate (%)": round(report.win_rate, 2),
        "Profit Factor": round(profit_factor, 2),
        "Sharpe Ratio": round(sharpe, 2),
        "Sortino Ratio": round(sortino, 2),
        "Calmar Ratio": round(calmar, 2),
        "Max Drawdown (%)": round(report.max_drawdown_pct, 2),
        "Avg Trade PnL ($)": round(float(np.mean(trades_pnl)), 2),
        "Profit/Loss Ratio": round(pl_ratio, 2),
        "CAGR (%)": round(cagr, 2),
        "Equity Curve": list(zip([t.isoformat() for t in pd.to_datetime(timestamps)], equities)),
    }


def run_all_backtests():
    print("=" * 75)
    print("ETHEREUM COMPREHENSIVE QUANTITATIVE BACKTEST SUITE")
    print("=" * 75)

    os.makedirs("results", exist_ok=True)
    os.makedirs("results/charts", exist_ok=True)

    strategies = {
        "AdaptiveSuperTrendRegimeETH": {
            "name": "SuperTrend Macro Regime & Pullback",
            "factory": lambda df: AdaptiveSuperTrendRegimeETH(df, ema_trend=200, ema_pullback=34, st_period=10, st_multiplier=3.0, rr_ratio=2.2),
            "warmup": 220,
        },
        "MultiConfluenceMeanReversionETH": {
            "name": "Extreme Confluence Mean Reversion (BB 2.5 + RSI)",
            "factory": lambda df: MultiConfluenceMeanReversionETH(df, bb_period=20, bb_std=2.5, rsi_period=14, rr_ratio=2.2),
            "warmup": 50,
        },
        "AggloReversalETH": {
            "name": "ML Agglomerative Clustering S/R Reversal",
            "factory": lambda df: AggloReversalETH(df, lookback=120, n_clusters=4, rr_ratio=2.5),
            "warmup": 130,
        },
        "HMAMACDMomentumETH": {
            "name": "Hull Moving Average (HMA) + MACD Momentum",
            "factory": lambda df: HMAMACDMomentumETH(df, hma_period=21, macd_fast=12, macd_slow=26, rr_ratio=2.5),
            "warmup": 100,
        },
        "EMATrendBreakoutETH": {
            "name": "EMA Trend & Volatility Breakout",
            "factory": lambda df: EMATrendBreakoutETH(df, fast_ema=34, slow_ema=144, atr_period=14, adx_threshold=18.0, rr_ratio=2.5),
            "warmup": 180,
        },
        "SMCLiquiditySweepETH": {
            "name": "SMC Liquidity Sweep & Fair Value Gap",
            "factory": lambda df: SMCLiquiditySweepETH(df, swing_bars=20, rr_ratio=3.0),
            "warmup": 60,
        },
    }

    datasets = {
        "15m": {
            "file": "data/ETHUSDT_15m.csv",
            "desc": "15-Minute Intraday (10,000 candles | May 2026 - Aug 2026)",
        },
        "1h": {
            "file": "data/ETHUSDT_1h.csv",
            "desc": "1-Hour Swing (8,000 candles | Sep 2025 - Aug 2026)",
        },
    }

    all_results = {}

    for tf_key, tf_info in datasets.items():
        print(f"\n>>> Running Backtests on {tf_info['desc']}...")
        df = pd.read_csv(tf_info["file"], index_col=0, parse_dates=True)
        all_results[tf_key] = {}

        plt.figure(figsize=(13, 6.5))
        plt.style.use("seaborn-v0_8-darkgrid" if "seaborn-v0_8-darkgrid" in plt.style.available else "default")

        # Buy & Hold benchmark calculation
        eth_start = float(df["close"].iloc[0])
        eth_hold_vals = (df["close"] / eth_start) * 10000.0
        plt.plot(df.index, eth_hold_vals, label=f"ETH Buy & Hold ({round(((df['close'].iloc[-1]/eth_start)-1)*100, 1)}%)", color="gray", linestyle="--", alpha=0.6, linewidth=1.5)

        for strat_key, strat_info in strategies.items():
            print(f"  [+] Testing {strat_info['name']}...")
            backtester = WalkForwardBacktester(
                strat_info["factory"],
                starting_equity=10000.0,
                risk_per_trade_pct=0.015,
                fee_rate=0.0004,
                slippage_bps=2.0,
            )
            report = backtester.run(df, symbol="ETHUSDT", warmup_bars=strat_info["warmup"])
            metrics = calculate_advanced_metrics(report, df, freq=tf_key)
            all_results[tf_key][strat_key] = {
                "name": strat_info["name"],
                "metrics": metrics,
                "trades_count": len(report.results),
                "outcomes": {
                    "targets": sum(1 for r in report.results if r.outcome == "target"),
                    "stops": sum(1 for r in report.results if r.outcome == "stop"),
                    "end_of_data": sum(1 for r in report.results if r.outcome == "end_of_data"),
                }
            }
            print(f"      Trades: {metrics['Trades']} | Win Rate: {metrics['Win Rate (%)']}% | Return: {metrics['Total Return (%)']}% | Sharpe: {metrics['Sharpe Ratio']} | MaxDD: {metrics['Max Drawdown (%)']}% | PF: {metrics['Profit Factor']}")

            eq_data = metrics.get("Equity Curve", [])
            if eq_data:
                eq_dates = [pd.to_datetime(x[0]) for x in eq_data]
                eq_vals = [x[1] for x in eq_data]
                plt.plot(eq_dates, eq_vals, label=f"{strat_info['name']} ({metrics['Total Return (%)']}%)", linewidth=1.9)

        plt.title(f"Ethereum Quantitative Trading Strategies — Equity Curve ({tf_info['desc']})", fontsize=13, fontweight="bold")
        plt.xlabel("Date", fontsize=11)
        plt.ylabel("Equity ($ USD)", fontsize=11)
        plt.legend(loc="upper left", frameon=True, fontsize=8.5)
        plt.tight_layout()
        chart_path = f"results/charts/equity_curves_{tf_key}.png"
        plt.savefig(chart_path, dpi=200)
        plt.close()
        print(f"  --> Saved equity curve chart to {chart_path}")

    with open("results/backtest_summary.json", "w") as f:
        json.dump(all_results, f, indent=2)
    print("\nAll backtests complete! Results saved to results/backtest_summary.json")
    return all_results


if __name__ == "__main__":
    run_all_backtests()
