import sys
import os
import json
import pandas as pd
from typing import Optional, Dict, Any, ClassVar
from dataclasses import dataclass, field

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from orbit.strategies.swing_strategy import SwingStrategyBTC
from orbit.backtesting.engine import WalkForwardBacktester

@dataclass
class BacktestSwingStrategy(SwingStrategyBTC):
    """
    Backtest wrapper for SwingStrategyBTC that mocks out Redis, Discord, 
    and Chart Generation to allow for standalone backtesting.
    """
    # Shared state across all instances to simulate Redis
    mock_redis: ClassVar[dict] = {"pivot_high": None, "pivot_low": None}

    def __post_init__(self):
        # Override to avoid connecting to real Redis
        from orbit.strategies.strategies_base import Strategy
        # Initialize base Strategy manually
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

    # Mock Discord/notifications
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

        df_4h = df_4h[self.data.resample("4h").size() == 16]
        if len(df_4h) == 0:
            return None
            
        close = df_4h['close'].iloc[-1]
        open_ = df_4h['open'].iloc[-1]

        self.atr = self.compute_atr(df_4h)

        if position_side:
            trail = self.update_trailing_sl_tp(close, position_side=position_side)
            if trail:
                return {
                    "signal": "UPDATE_SL_TP",
                    "stop_loss": trail["stop_loss"],
                    "take_profit": trail["take_profit"],
                }

        # Backtest-compatible timing check
        now = self.data.index[-1]
        last_time = df_4h.index[-1]
        
        # In backtest mode: generate signal once per 4H candle, when the
        # offset falls in [3h30m .. 4h] (i.e. 12600–14400 seconds).
        # The production code uses exact == 13500s which is too brittle.
        offset_seconds = (now - last_time).total_seconds()
        if not (12600 <= offset_seconds <= 14400):
            return None

        # -----------------------
        # Pivots
        # -----------------------
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

        long_signal  = (self.last_sw_h is not None) and (close > self.last_sw_h) and (open_ < self.last_sw_h)
        short_signal = (self.last_sw_l is not None) and (close < self.last_sw_l) and (open_ > self.last_sw_l)

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
                "pattern": "Long Swing"
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
                "pattern": "Short Swing"
            }

        return None

def main():
    data_path = "/root/agy-workspace/Orbit/data/BTCUSDT_15m.csv"
    if not os.path.exists(data_path):
        print(f"Data file not found at {data_path}. Please ensure it is downloaded.")
        return

    # Load data
    print("Loading data...")
    df = pd.read_csv(data_path)
    
    # Normalize columns
    df.columns = [c.lower() for c in df.columns]
    
    # Ensure datetime index
    time_col = "timestamp" if "timestamp" in df.columns else "date" if "date" in df.columns else "time" if "time" in df.columns else None
    if time_col:
        df[time_col] = pd.to_datetime(df[time_col])
        df.set_index(time_col, inplace=True)
    
    df.sort_index(inplace=True)
    
    # Ensure all required columns exist
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(df.columns):
        print(f"Error: Missing required columns. Found {list(df.columns)}")
        return

    # Reset mock redis before run
    BacktestSwingStrategy.mock_redis = {"pivot_high": None, "pivot_low": None}

    backtester = WalkForwardBacktester(
        strategy_factory=BacktestSwingStrategy,
        starting_equity=10_000.0,
        risk_per_trade_pct=0.01,
        fee_rate=0.0004,
        slippage_bps=2.0
    )

    print("Running backtest (this might take a while)...")
    report = backtester.run(df, symbol="BTCUSDT", warmup_bars=1000)

    # Print results
    print("\n=== BACKTEST RESULTS ===")
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
    out_dir = "/root/agy-workspace/Orbit/results"
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, "btc_swing_backtest.json")
    
    def default_serializer(obj):
        if isinstance(obj, pd.Timestamp):
            return obj.isoformat()
        raise TypeError(f"Type {type(obj)} not serializable")
        
    with open(out_file, "w") as f:
        json.dump(report.to_dict(), f, indent=2, default=default_serializer)
    
    print(f"\nResults saved to {out_file}")

if __name__ == "__main__":
    main()
