# BTCUSDT Hourly Breakout Strategy

## Objective

Increase BTC trade frequency relative to the previous four-hour pivot strategy
while retaining positive results outside the parameter-development period.

## Rules

- Timeframe: 1 hour (four complete 15-minute production candles)
- Long: close above the prior 12-bar high and above EMA(50)
- Short: close below the prior 12-bar low and below EMA(50)
- Initial stop: 2.5 ATR(14)
- Target: 3 times initial risk
- Trailing stop: 12-bar high/low chandelier, offset by 2.5 ATR
- Sizing assumption: 1% account risk per trade

All breakout levels are shifted by one bar. The current bar therefore cannot
change the level it is attempting to break.

## Research method

Hourly Binance BTCUSDT candles from 2021-01-01 through 2026-08-24 were split
chronologically: 70% for development and 30% for holdout validation. The search
compared hourly breakout and EMA-pullback entries across multiple EMA, breakout,
ATR-stop, and reward/risk values. It modeled 0.04% fees per side, two basis
points of slippage per side, next-bar-open entries, one open position, and a
stop-first assumption when stop and target occurred within the same candle.

| Period | Return | Trades | Win rate | Profit factor | Max drawdown |
| --- | ---: | ---: | ---: | ---: | ---: |
| Development (70%) | 36.54% | 499 | 28.3% | 1.10 | 18.29% |
| Holdout (30%) | 21.41% | 226 | 29.2% | 1.14 | 18.79% |

These are historical simulation results, not expected returns. The modest
profit factor means execution costs and regime changes matter materially. Keep
the strategy on testnet until forward results include a statistically useful
sample, and pause it if realized costs or drawdown exceed the modeled range.
