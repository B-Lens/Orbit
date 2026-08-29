# BTCUSDT Support/Resistance Strategy

BTCUSDT uses `BTCSRStrategy`, a one-hour support/resistance strategy registered
in `config/strategies.yaml`. Production supplies 15-minute candles; the strategy
aggregates four complete candles before evaluating a new hourly signal. It needs
at least 250 completed hourly bars and uses up to 500 bars when locating levels.

The strategy is currently configured for **testnet**. The committed backtest is
not profitable, so it should not be promoted to live execution without further
research and forward validation.

## Level construction

The strategy detects swing highs and lows with fractal pivots and merges pivots
whose prices are within one current ATR. A cluster must have at least two
touches. It also considers $2,500 round-number levels within 20% of the current
price, then retains the eight nearest zones that are within 10% of price.

Each zone receives a confluence score based on:

- the number of clustered touches;
- proximity to the current price; and
- a bonus for round-number levels.

The current implementation does not include a volume-profile calculation in
the zone score.

## Trade setups

Signals are evaluated in this order:

1. **Flip retest:** a recently crossed level is retested from the opposite side
   on relative volume below 1.2 and prints a rejection candle.
2. **Bounce:** price rejects a support or resistance zone with a long wick,
   passes the RSI filter, and remains reasonably close to the EMA(200) trend.
3. **Breakout:** price closes through a zone in the EMA(200) trend direction on
   relative volume of at least 1.3.

Only the first qualifying setup produces a signal. Long and short entries are
supported.

## Risk management

- Stops are placed 1.5 ATR beyond the relevant zone.
- Targets use a 2.5:1 reward-to-risk ratio.
- Bounce signals are rejected when stop distance exceeds 4 ATR.
- Open positions receive a trailing-stop update based on the extreme of the
  latest 12 hourly bars minus or plus 1.5 ATR.

## Reproducing the backtest

Download the one-hour Binance candles and run the walk-forward backtest from
the repository root:

```bash
poetry run python scripts/download_btc_data.py \
  --start 2021-01-01 --end 2026-08-25
poetry run python scripts/run_btc_sr_backtest.py --equity 10000 --risk 0.01
poetry run pytest tests/test_btc_sr_strategy.py -q
```

The runner uses next-bar-open execution, 0.04% fees per fill, two basis points
of slippage, risk-based position sizing, and conservative stop-first resolution
when the stop and target are both touched in one candle. Results are written to
`results/btc_sr_backtest.json`.

## Committed result

The committed result covers 2021-01-01 through 2026-08-25 with $10,000 starting
equity and 1% equity risk per trade.

| Metric | Result |
| --- | ---: |
| Total return | -6.21% |
| Net PnL | -$620.94 |
| Trades | 469 |
| Win rate | 30.28% |
| Profit factor | 0.98 |
| Maximum drawdown | 35.56% |
| CAGR | -1.13% |
| Sharpe ratio | -0.01 |
| Sortino ratio | -0.50 |

| Setup | Trades | Win rate | Net PnL |
| --- | ---: | ---: | ---: |
| Bounce | 225 | 31.11% | $169.39 |
| Flip retest | 244 | 29.51% | -$790.33 |
| Breakout | 0 | N/A | $0.00 |

These figures are a full-period research result, not a development/holdout
comparison. In particular, the negative return, sub-1.0 profit factor, large
drawdown, and loss from flip-retest trades mean the result does not justify live
deployment. Before promotion, validate changes on a separately held-out period,
then collect a meaningful testnet sample and define rollback criteria.
