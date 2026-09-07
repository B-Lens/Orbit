import pandas as pd
import numpy as np
import itertools
import os
import time

def backtest(df, fast, slow, atr_m, rr):
    df = df.copy()
    df['ema_fast'] = df['close'].ewm(span=fast, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=slow, adjust=False).mean()
    
    prev_close = df['close'].shift(1)
    tr1 = df['high'] - df['low']
    tr2 = (df['high'] - prev_close).abs()
    tr3 = (df['low'] - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()
    
    df['bullish'] = (df['ema_fast'] > df['ema_slow']) & (df['ema_fast'].shift(1) <= df['ema_slow'].shift(1))
    df['bearish'] = (df['ema_fast'] < df['ema_slow']) & (df['ema_fast'].shift(1) >= df['ema_slow'].shift(1))
    
    equity = 10000.0
    risk_pct = 0.02
    fee = 0.0004
    slippage = 0.0002
    
    trades = 0
    wins = 0
    net_pnl = 0
    
    in_position = False
    entry_price = 0
    sl = 0
    tp = 0
    pos_type = 0
    qty = 0
    
    # Fast iteration
    closes = df['close'].values
    opens = df['open'].values
    highs = df['high'].values
    lows = df['low'].values
    atrs = df['atr'].values
    bullish = df['bullish'].values
    bearish = df['bearish'].values
    
    for i in range(1, len(df)):
        if in_position:
            # Check exit
            curr_low = lows[i]
            curr_high = highs[i]
            curr_close = closes[i]
            
            exit_price = None
            if pos_type == 1:
                if curr_low <= sl:
                    exit_price = sl * (1 - slippage)
                elif curr_high >= tp:
                    exit_price = tp * (1 - slippage)
            else:
                if curr_high >= sl:
                    exit_price = sl * (1 + slippage)
                elif curr_low <= tp:
                    exit_price = tp * (1 + slippage)
                    
            if exit_price is not None:
                # Trade closed
                direction = 1 if pos_type == 1 else -1
                gross = (exit_price - entry_price) * qty * direction
                costs = (entry_price + exit_price) * qty * fee
                net = gross - costs
                equity += net
                net_pnl += net
                trades += 1
                if net > 0:
                    wins += 1
                in_position = False
        
        if not in_position and not np.isnan(atrs[i-1]):
            # Check entry
            if bullish[i-1]:
                pos_type = 1
                entry_price = opens[i] * (1 + slippage)
                sl = opens[i] - (atrs[i-1] * atr_m)
                tp = opens[i] + (atrs[i-1] * atr_m * rr)
                risk_per_unit = abs(entry_price - sl)
                if risk_per_unit > 0:
                    qty = (equity * risk_pct) / risk_per_unit
                    in_position = True
            elif bearish[i-1]:
                pos_type = -1
                entry_price = opens[i] * (1 - slippage)
                sl = opens[i] + (atrs[i-1] * atr_m)
                tp = opens[i] - (atrs[i-1] * atr_m * rr)
                risk_per_unit = abs(entry_price - sl)
                if risk_per_unit > 0:
                    qty = (equity * risk_pct) / risk_per_unit
                    in_position = True

    win_rate = (wins / trades * 100) if trades > 0 else 0
    return net_pnl, trades, win_rate

def main():
    data_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'BNBUSDT_1h.csv')
    df = pd.read_csv(data_path)
    
    fast_mas = [10, 20, 30]
    slow_mas = [50, 100, 200]
    atr_multis = [1.5, 2.0, 3.0]
    rrs = [1.5, 2.0, 3.0]
    
    best_pnl = -float('inf')
    best_params = None
    
    for f, s, am, rr in itertools.product(fast_mas, slow_mas, atr_multis, rrs):
        if f >= s: continue
        pnl, trades, wr = backtest(df, f, s, am, rr)
        if pnl > best_pnl and trades > 10:
            best_pnl = pnl
            best_params = (f, s, am, rr, trades, wr)
    
    print(f"BEST PARAMS: Fast={best_params[0]}, Slow={best_params[1]}, ATR={best_params[2]}, RR={best_params[3]}")
    print(f"PnL: {best_pnl:.2f}, Trades: {best_params[4]}, Win Rate: {best_params[5]:.2f}%")

if __name__ == '__main__':
    main()
