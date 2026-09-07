import argparse
import itertools
import pandas as pd
import numpy as np
from typing import Any, Dict, Optional
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

from orbit.backtesting.engine import WalkForwardBacktester
from orbit.strategies.strategies_base import Strategy
import logging

logging.basicConfig(level=logging.INFO)

class BNBParametricStrategy(Strategy):
    data: pd.DataFrame
    
    # default params, we will monkey patch them
    ma_fast: int = 10
    ma_slow: int = 50
    atr_period: int = 14
    atr_stop_multi: float = 2.0
    rr_ratio: float = 2.0
    
    def __post_init__(self) -> None:
        super().__init__(self.data)
        
    def generate_signals(self, symbol: Optional[str] = None, position_side: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if len(self.data) < max(self.ma_slow, self.atr_period) + 1:
            return None
            
        df = self.data
        close = df['close']
        
        # Calculate indicators on the fly for the slice
        ema_fast = close.ewm(span=self.ma_fast, adjust=False).mean()
        ema_slow = close.ewm(span=self.ma_slow, adjust=False).mean()
        
        previous_close = close.shift(1)
        true_range = pd.concat([
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ], axis=1).max(axis=1)
        atr = true_range.rolling(window=self.atr_period).mean()
        
        current_close = float(close.iloc[-1])
        current_atr = float(atr.iloc[-1])
        
        if pd.isna(current_atr) or current_atr == 0:
            return None

        # Long only or both? Let's do both
        is_bullish = ema_fast.iloc[-1] > ema_slow.iloc[-1] and ema_fast.iloc[-2] <= ema_slow.iloc[-2]
        is_bearish = ema_fast.iloc[-1] < ema_slow.iloc[-1] and ema_fast.iloc[-2] >= ema_slow.iloc[-2]
        
        if position_side:
            # Trailing stop could go here, but engine just uses fixed TP/SL initially
            return {"signal": "UPDATE_SL_TP", "stop_loss": 0, "take_profit": 0}
            
        if is_bullish:
            sl = current_close - (current_atr * self.atr_stop_multi)
            tp = current_close + (current_atr * self.atr_stop_multi * self.rr_ratio)
            return {
                "signal": "BUY",
                "entry_price": current_close,
                "stop_loss": sl,
                "take_profit": tp,
                "pattern": f"EMA{self.ma_fast}/{self.ma_slow} Crossover"
            }
        elif is_bearish:
            sl = current_close + (current_atr * self.atr_stop_multi)
            tp = current_close - (current_atr * self.atr_stop_multi * self.rr_ratio)
            return {
                "signal": "SELL",
                "entry_price": current_close,
                "stop_loss": sl,
                "take_profit": tp,
                "pattern": f"EMA{self.ma_fast}/{self.ma_slow} Crossover"
            }
        return None

def main():
    data_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'BNBUSDT_1h_small.csv')
    df = pd.read_csv(data_path)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True)
    
    # We will test a grid of parameters
    fast_mas = [10, 20]
    slow_mas = [50, 100, 200]
    atr_multis = [1.5, 2.0, 3.0]
    rrs = [1.5, 2.0, 3.0]
    
    best_pnl = -float('inf')
    best_params = None
    best_report = None
    
    count = 0
    total = len(fast_mas) * len(slow_mas) * len(atr_multis) * len(rrs)
    
    for fm, sm, am, rr in itertools.product(fast_mas, slow_mas, atr_multis, rrs):
        count += 1
        print(f"Testing {count}/{total}: Fast={fm}, Slow={sm}, ATRm={am}, RR={rr}")
        
        class GridStrategy(BNBParametricStrategy):
            ma_fast = fm
            ma_slow = sm
            atr_stop_multi = am
            rr_ratio = rr

        backtester = WalkForwardBacktester(
            strategy_factory=GridStrategy,
            starting_equity=10000.0,
            risk_per_trade_pct=0.02
        )
        report = backtester.run(df, symbol="BNBUSDT", warmup_bars=sm+5)
        
        print(f"PnL: {report.net_pnl:.2f}, Trades: {report.trades}, Win Rate: {report.win_rate:.2f}%")
        if report.net_pnl > best_pnl:
            best_pnl = report.net_pnl
            best_params = (fm, sm, am, rr)
            best_report = report

    print("\n================== BEST PARAMS ==================")
    print(f"Fast MA: {best_params[0]}")
    print(f"Slow MA: {best_params[1]}")
    print(f"ATR Multiplier: {best_params[2]}")
    print(f"Reward/Risk: {best_params[3]}")
    print(f"Net PnL: {best_report.net_pnl:.2f}")
    print(f"Win Rate: {best_report.win_rate:.2f}%")
    print(f"Total Trades: {best_report.trades}")
    print(f"Max Drawdown: {best_report.max_drawdown_pct:.2f}%")

if __name__ == '__main__':
    main()
