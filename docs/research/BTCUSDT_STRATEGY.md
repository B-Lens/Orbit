# BTCUSDT Strategy

BTC uses a non-repainting 1-hour breakout strategy:

- Buy above the previous 12-bar high when price is above EMA(50).
- Sell below the previous 12-bar low when price is below EMA(50).
- Use a 2.5 ATR(14) stop, 3R target, and ATR chandelier trailing stop.
- Build each hourly bar from four complete 15-minute production candles.

## Backtest comparison

Binance BTCUSDT data from 2021-01-01 through 2026-08-24 used 1% risk per
trade, next-bar-open execution, fees, slippage, and conservative stop-first
resolution.

| Period | Previous 4H return/trades | New 1H return/trades |
| --- | ---: | ---: |
| Development 70% | -16.26% / 258 | 33.22% / 507 |
| Holdout 30% | 11.50% / 85 | 22.48% / 222 |
| Full period | -6.63% / 343 | 63.31% / 729 |

The new strategy added 386 trades and improved full-period return by 69.94
percentage points. Its holdout drawdown was higher, however: 16.89% versus
9.44%. Keep it on testnet until forward results provide a meaningful sample.
