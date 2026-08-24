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
ATR-stop, and reward/risk values.

The comparison reproduces each strategy's production signal calculations, then
uses the shared backtester contract: entry at the next bar's open, 1% equity
risk per trade, 0.04% fees per side, two basis points of slippage per side, one
open position, and stop-first resolution when stop and target occur within the
same candle. Each split starts with $10,000, so development and holdout returns
are independently comparable rather than parts of one continuous equity curve.

## Comparison with the previous strategy

| Period | Strategy | Return | Trades | Win rate | Profit factor | Max drawdown |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Development (70%) | Previous 4H pivot | -16.26% | 258 | 33.33% | 0.90 | 28.07% |
| Development (70%) | New 1H breakout | 33.22% | 507 | 28.40% | 1.08 | 18.08% |
| Holdout (30%) | Previous 4H pivot | 11.50% | 85 | 40.00% | 1.20 | 9.44% |
| Holdout (30%) | New 1H breakout | 22.48% | 222 | 29.73% | 1.12 | 16.89% |
| Full period | Previous 4H pivot | -6.63% | 343 | 34.99% | 0.97 | 28.07% |
| Full period | New 1H breakout | 63.31% | 729 | 28.81% | 1.09 | 18.08% |

Over the full period, the new strategy improves return by 69.94 percentage
points and adds 386 trades. On the untouched holdout it improves return by
10.98 percentage points and adds 137 trades. Its lower win rate is expected
from the 3R payoff design; profitability depends on fewer, larger winners.

The holdout comparison also shows the trade-off: the new strategy has higher
return and frequency, but the previous strategy has a higher holdout profit
factor and lower holdout drawdown. The new strategy's advantage is therefore
not uniform across every risk metric.

These are historical simulation results, not expected returns. The modest
profit factor means execution costs and regime changes matter materially. Keep
the strategy on testnet until forward results include a statistically useful
sample, and pause it if realized costs or drawdown exceed the modeled range.
