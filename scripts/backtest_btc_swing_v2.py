"""
Backtest script for the improved SwingStrategyBTC_V2.

Runs the V2 strategy (with EMA200, ADX, RSI, and volume filters) through
the WalkForwardBacktester and compares results against the V1 baseline.
"""

import sys
import os
import json
import pandas as pd
from typing import Optional, Dict, Any, ClassVar
from dataclasses import dataclass, field

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from orbit.strategies.swing_strategy_v2 import SwingStrategyBTC_V2
from orbit.backtesting.engine import WalkForwardBacktester


@dataclass
class BacktestSwingV2(SwingStrategyBTC_V2):
    """
    Backtest wrapper for SwingStrategyBTC_V2 that mocks out Redis, Discord,
    and chart generation to allow standalone backtesting.
    """
    mock_redis: ClassVar[dict] = {"pivot_high": None, "pivot_low": None}

    def __post_init__(self):
        from orbit.strategies.strategies_base import Strategy
        Strategy.__init__(self, self.data)

        self.redis_client = None
        self.last_sw_h = None
        self.last_sw_l = None

    def _load_last_pivots(self):
        return self.mock_redis.get("pivot_high"), self.mock_redis.get("pivot_low")

    def _save_pivots(self, ph, pl):
        if ph is not None:
            self.mock_redis["pivot_high"] = ph
        if pl is not None:
            self.mock_redis["pivot_low"] = pl

    # Mock Discord / notifications
    def send_params(self, *args, **kwargs):
        pass

    def send_levels_info(self, *args, **kwargs):
        pass

    def send_parameters(self, *args, **kwargs):
        pass

    def generate_signals(self, symbol=None, position_side=None) -> Optional[Dict[str, Any]]:
        lookup = 168

        df_4h = (
            self.data.resample("4h")
            .agg({
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            })
        )

        df_4h = df_4h.dropna()

        if len(df_4h) < lookup:
            return None

        lookback_4h = df_4h.iloc[-lookup:]
        close = df_4h['close'].iloc[-1]
        open_ = df_4h['open'].iloc[-1]

        # Indicators
        self.atr = self.compute_atr(df_4h)
        ema200 = self.compute_ema(df_4h['close'], period=200).iloc[-1]
        adx = self.compute_adx(df_4h, period=14).iloc[-1]
        rsi = self.compute_rsi(df_4h['close'], period=14).iloc[-1]
        current_volume = df_4h['volume'].iloc[-1]
        volume_ma20 = df_4h['volume'].rolling(window=20).mean().iloc[-1]

        if position_side:
            trail = self.update_trailing_sl_tp(close, position_side=position_side)
            if trail:
                return {
                    "signal": "UPDATE_SL_TP",
                    "stop_loss": trail["stop_loss"],
                    "take_profit": trail["take_profit"],
                }

        # Backtest timing: generate signal once per 4H candle near completion
        now = self.data.index[-1]
        last_time = df_4h.index[-1]
        offset_seconds = (now - last_time).total_seconds()
        if not (12600 <= offset_seconds <= 14400):
            return None

        # Pivots
        highs = df_4h["high"].tolist()[-(2*self.n+1):]
        lows  = df_4h["low"].tolist()[-(2*self.n+1):]

        self.last_sw_h, self.last_sw_l = self._load_last_pivots()

        ph = self.pivot_high_centered(highs, self.n, self.n)
        pl = self.pivot_low_centered(lows, self.n, self.n)

        if ph is not None:
            self.last_sw_h = ph
        if pl is not None:
            self.last_sw_l = pl

        self._save_pivots(self.last_sw_h, self.last_sw_l)

        # Filters
        is_long_trend = close > ema200
        is_short_trend = close < ema200
        is_trending_market = adx > 20
        is_valid_long_rsi = 40 <= rsi <= 65
        is_valid_short_rsi = 35 <= rsi <= 60
        has_volume_confirmation = current_volume > (1.2 * volume_ma20)

        # Entry conditions
        price_breakout_long = (self.last_sw_h is not None) and (close > self.last_sw_h) and (open_ < self.last_sw_h)
        price_breakout_short = (self.last_sw_l is not None) and (close < self.last_sw_l) and (open_ > self.last_sw_l)

        long_signal = (
            price_breakout_long and
            is_long_trend and
            is_trending_market and
            is_valid_long_rsi and
            has_volume_confirmation
        )

        short_signal = (
            price_breakout_short and
            is_short_trend and
            is_trending_market and
            is_valid_short_rsi and
            has_volume_confirmation
        )

        stop, target = None, None

        if long_signal:
            stop, target = self.compute_long_sl_tp(close)
            return {
                "signal": "BUY",
                "entry_price": close,
                "stop_loss": stop,
                "take_profit": target,
                "chart_path": None,
                "chart_path_raw": None,
                "pattern": "Long Swing V2"
            }

        if short_signal:
            stop, target = self.compute_short_sl_tp(close)
            return {
                "signal": "SELL",
                "entry_price": close,
                "stop_loss": stop,
                "take_profit": target,
                "chart_path": None,
                "chart_path_raw": None,
                "pattern": "Short Swing V2"
            }

        return None


def main():
    data_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'BTCUSDT_15m.csv')
    data_path = os.path.abspath(data_path)

    if not os.path.exists(data_path):
        print(f"Data file not found at {data_path}.")
        return

    print("Loading data...")
    df = pd.read_csv(data_path)
    df.columns = [c.lower() for c in df.columns]

    time_col = "timestamp" if "timestamp" in df.columns else "date" if "date" in df.columns else None
    if time_col:
        df[time_col] = pd.to_datetime(df[time_col])
        df.set_index(time_col, inplace=True)

    df.sort_index(inplace=True)

    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(df.columns):
        print(f"Error: Missing required columns. Found {list(df.columns)}")
        return

    # Reset mock redis before run
    BacktestSwingV2.mock_redis = {"pivot_high": None, "pivot_low": None}

    backtester = WalkForwardBacktester(
        strategy_factory=BacktestSwingV2,
        starting_equity=10_000.0,
        risk_per_trade_pct=0.01,
        fee_rate=0.0004,
        slippage_bps=2.0
    )

    print("Running V2 backtest (this might take a while)...")
    report = backtester.run(df, symbol="BTCUSDT", warmup_bars=1000)

    # Print results
    print("\n=== BACKTEST RESULTS (V2) ===")
    print(f"Net PnL: ${report.net_pnl:.2f}")
    print(f"Return %: {report.return_pct:.2f}%")
    print(f"Win Rate: {report.win_rate:.2f}%")
    print(f"Profit Factor: {report.profit_factor if report.profit_factor else 0:.2f}")
    print(f"Max Drawdown: {report.max_drawdown_pct:.2f}%")
    print(f"Total Trades: {report.trades}")
    print(f"Wins: {report.wins}")
    print(f"Losses: {report.losses}")

    if report.trades > 0:
        avg_pnl = sum(r.net_pnl for r in report.results) / report.trades
        print(f"Average Trade PnL: ${avg_pnl:.2f}")

    print("\nTrades:")
    for i, r in enumerate(report.results, 1):
        print(f"{i}. {r.side} at {r.entry_time} (Entry: {r.entry_price:.2f}) -> Exit at {r.exit_time} (Exit: {r.exit_price:.2f}) | PnL: ${r.net_pnl:.2f} ({r.outcome})")

    # Save to file
    out_dir = os.path.join(os.path.dirname(__file__), '..', 'results')
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, "btc_swing_v2_backtest.json")

    def default_serializer(obj):
        if isinstance(obj, pd.Timestamp):
            return obj.isoformat()
        raise TypeError(f"Type {type(obj)} not serializable")

    with open(out_file, "w") as f:
        json.dump(report.to_dict(), f, indent=2, default=default_serializer)

    print(f"\nResults saved to {out_file}")

    # Load V1 results for comparison if available
    v1_path = os.path.join(out_dir, "btc_swing_backtest.json")
    if os.path.exists(v1_path):
        with open(v1_path) as f:
            v1 = json.load(f)
        print("\n=== V1 vs V2 COMPARISON ===")
        print(f"{'Metric':<25} {'V1':>12} {'V2':>12}")
        print("-" * 50)
        print(f"{'Net PnL ($)':<25} {v1['net_pnl']:>12.2f} {report.net_pnl:>12.2f}")
        print(f"{'Return (%)':<25} {v1['return_pct']:>12.2f} {report.return_pct:>12.2f}")
        print(f"{'Win Rate (%)':<25} {v1['win_rate']:>12.2f} {report.win_rate:>12.2f}")
        v1_pf = v1.get('profit_factor') or 0
        v2_pf = report.profit_factor or 0
        print(f"{'Profit Factor':<25} {v1_pf:>12.2f} {v2_pf:>12.2f}")
        print(f"{'Max Drawdown (%)':<25} {v1['max_drawdown_pct']:>12.2f} {report.max_drawdown_pct:>12.2f}")
        print(f"{'Total Trades':<25} {v1['trades']:>12} {report.trades:>12}")
        print(f"{'Wins':<25} {v1['wins']:>12} {report.wins:>12}")
        print(f"{'Losses':<25} {v1['losses']:>12} {report.losses:>12}")


if __name__ == "__main__":
    main()
