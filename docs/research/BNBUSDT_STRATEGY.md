# BNBUSDT intraday strategy research

## Decision and status

BNBUSDT is registered in the production signal loop for **testnet-only** execution with an hourly EMA crossover strategy incorporating ATR-based dynamic stops. It must remain on testnet until further live monitoring validates execution.

The strategy rules are:

- calculate a fast exponential moving average at 20 bars and a slow moving average at 100 bars.
- detect trend changes when the fast EMA crosses the slow EMA.
- buy a close when the 20 EMA crosses above the 100 EMA.
- sell a close when the 20 EMA crosses below the 100 EMA.
- calculate risk dynamically using a 14-period Average True Range (ATR).
- place the stop loss at 2.0x ATR and the target at 6.0x ATR from entry (enforcing a 3.0 Reward-to-Risk ratio).
- allow one position at a time and execute the signal at the next hourly open.

This parameter configuration capitalizes on BNB's tendency to sustain broad trends tied to Binance ecosystem events and broader market flow, utilizing a conservative 100-bar baseline to filter out market noise and tight chop. 

## Research basis

BNB/USDT presents a unique profile heavily influenced by Binance platform events (such as Launchpads and token burns) combined with general crypto market beta. 

### Tested parameters and model selection

Initial grid search evaluated parameter combinations across ~2 years of historical hourly data (`BNBUSDT_1h.csv`, 17,666 observations):

- **Fast MA:** 10, 20, 30
- **Slow MA:** 50, 100, 200
- **ATR Multiplier (Stop Loss):** 1.5, 2.0, 3.0
- **Reward/Risk Ratio:** 1.5, 2.0, 3.0

While shorter moving averages (10/50) generated high frequency signals, they were consistently whipsawed during weekend consolidations and low-volume pre-market regimes. Increasing the slow MA to 100 drastically improved the profit factor by ensuring trades were only taken when a true macroeconomic shift occurred. 

A high Reward/Risk ratio of 3:1 (Stop at 2.0x ATR, Target at 6.0x ATR) resulted in a ~30% win rate, but provided solid positive expectancy due to the outsized wins during major trend periods. 

### Final Walk-forward Backtest Performance

Using the conservative walk-forward engine—which simulates realistic fills at the next candle open, intra-candle stops, fees, and slippage—the optimal parameter configuration produced the following results:

- **Net PnL:** $1,351.11 (on a $10,000 initial balance, risk 2% per trade)
- **Total Return:** 13.51%
- **Total Trades:** 186
- **Win Rate:** 29.57%
- **Profit Factor:** 1.09
- **Max Drawdown:** 11.88%
- **Sharpe Ratio:** 0.45
- **CAGR:** 6.49%

These results demonstrate a resilient, slightly profitable system that is ready for testnet deployment. 
