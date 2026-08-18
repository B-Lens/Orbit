import sys
import os
import pandas as pd
import numpy as np

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(project_root, 'src'))

from orbit.backtesting.engine import WalkForwardBacktester
from orbit.strategies.xauusdt_strategy import XAUUSDTStrategy

def run_optimization():
    data_path = os.path.join(project_root, 'data', 'XAUUSDT_15m.csv')
    data = pd.read_csv(data_path)
    if 'timestamp' in data.columns:
        data['timestamp'] = pd.to_datetime(data['timestamp'])
        data.set_index('timestamp', inplace=True)
    else:
        data.index = pd.to_datetime(data.iloc[:, 0])
        data = data.iloc[:, 1:]

    best_pnl = -float('inf')
    best_params = None

    # We will subclass XAUUSDTStrategy to inject parameters
    
    for ema in [50, 100, 200]:
        for rsi_thresh in [(30, 70), (40, 60), (20, 80)]:
            for sl_mult in [1.0, 1.5, 2.0, 3.0]:
                for tp_mult in [1.5, 2.0, 3.0, 4.0]:
                    if tp_mult <= sl_mult:
                        continue
                        
                    class TunedStrategy(XAUUSDTStrategy):
                        def __init__(self, df, symbol="XAUUSDT"):
                            super().__init__(df, symbol)
                            self.ema_period = ema
                            self.rsi_period = 14
                            self.atr_period = 14
                            self.atr_multiplier_sl = sl_mult
                            self.atr_multiplier_tp = tp_mult
                            self.rsi_lower = rsi_thresh[0]
                            self.rsi_upper = rsi_thresh[1]

                        def generate_signals(self, symbol=None, position_side=None):
                            if len(self.data) < self.ema_period:
                                return None
                            df = self.data.copy()
                            ema_val = self.compute_ema(df['close'], period=self.ema_period)
                            rsi = self.compute_rsi(df['close'], period=self.rsi_period)
                            atr = self.compute_atr(df, period=self.atr_period)
                            
                            current_close = df['close'].iloc[-1]
                            current_ema = ema_val.iloc[-1]
                            current_rsi = rsi.iloc[-1]
                            prev_rsi = rsi.iloc[-2]
                            current_atr = atr.iloc[-1]
                            
                            if not symbol:
                                symbol = self.symbol
                                
                            if position_side:
                                return None

                            uptrend = current_close > current_ema
                            downtrend = current_close < current_ema
                            
                            long_signal = uptrend and prev_rsi < self.rsi_lower and current_rsi >= self.rsi_lower
                            short_signal = downtrend and prev_rsi > self.rsi_upper and current_rsi <= self.rsi_upper
                            
                            if long_signal:
                                return {
                                    "signal": "BUY",
                                    "entry_price": current_close,
                                    "stop_loss": current_close - (current_atr * self.atr_multiplier_sl),
                                    "take_profit": current_close + (current_atr * self.atr_multiplier_tp),
                                    "pattern": "XAUUSDT Tuned Reversal"
                                }
                            if short_signal:
                                return {
                                    "signal": "SELL",
                                    "entry_price": current_close,
                                    "stop_loss": current_close + (current_atr * self.atr_multiplier_sl),
                                    "take_profit": current_close - (current_atr * self.atr_multiplier_tp),
                                    "pattern": "XAUUSDT Tuned Reversal"
                                }
                            return None

                    backtester = WalkForwardBacktester(
                        strategy_factory=TunedStrategy,
                        starting_equity=10000.0,
                        risk_per_trade_pct=0.01,
                    )
                    
                    report = backtester.run(data, symbol="XAUUSDT", warmup_bars=250)
                    
                    pnl = report.net_pnl
                    if pnl > best_pnl:
                        best_pnl = pnl
                        best_params = {
                            "ema": ema,
                            "rsi_lower": rsi_thresh[0],
                            "rsi_upper": rsi_thresh[1],
                            "sl_mult": sl_mult,
                            "tp_mult": tp_mult,
                            "trades": report.trades,
                            "win_rate": report.win_rate,
                            "profit_factor": report.profit_factor
                        }
                        print(f"New best: {best_pnl} with {best_params}")

    print("\nBest Parameters found:")
    print(best_params)
    print(f"Best PnL: {best_pnl}")

if __name__ == '__main__':
    run_optimization()
