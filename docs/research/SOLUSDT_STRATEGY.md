# SOLUSDT intraday strategy research

## Decision and status

SOLUSDT has an hourly trend-breakout strategy for research and backtesting, but
it is not registered in the production signal loop. It remains a testnet
monitor-only asset so existing positions can be reconciled. The shared runtime
candle cache currently contains Binance Spot data; activation must wait until the
loop can consume Binance Futures candles from the selected execution environment.
The strategy rules are:

- buy a close above the previous 48-hour high while above EMA(200)
- sell a close below the previous 48-hour low while below EMA(200)
- require volume above 1.2 times the preceding 24-hour average
- place the stop at 2 ATR(14) and the target at four times that initial risk
- allow one position at a time and execute the signal at the next hourly open

The 48-hour range reduces sensitivity to single-hour noise, EMA(200) prevents
countertrend entries, the volume gate filters quiet breaks, and ATR scales risk
levels as volatility changes.
These rules are symmetric; the strategy does not assume SOL must appreciate.

## Research basis

Crypto trades continuously, but activity is not uniform through the day.
Published research finds hourly volume and volatility periodicity, increased
activity during European and US market hours, and evidence of both momentum and
reversal in intraday crypto returns. That evidence motivates an hourly sampling
interval, a breakout confirmation rather than prediction from one candle, and
volatility-normalized exits. It does not establish that the published effects
will persist in SOL.

Sources:

- [Binance USD-M Futures market-data API](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Kline-Candlestick-Data)
- [Binance USD-M Futures exchange information](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information)
- [Intraday return predictability in cryptocurrency markets](https://doi.org/10.1016/j.qref.2022.09.009)
- [Time-of-day periodicities of Bitcoin volume and volatility](https://doi.org/10.1016/j.frl.2019.07.016)

The local empirical study used 17,520 SOLUSDT perpetual-futures hourly candles
downloaded from Binance, from 2024-09-02 17:00 UTC through 2026-09-02 16:00 UTC.
The sample's median hourly high-low range was approximately 0.95% and annualized
close-to-close volatility approximately 78%. Average absolute returns and volume
were highest around 13:00-17:00 UTC. These descriptive values are sample-specific.

## Candidate selection

The exploratory comparison varied the Donchian lookback (24/48 hours), EMA
filter (100/200), volume threshold (none/1.2 times its 24-hour mean), stop width
(1.5/2 ATR), and reward/risk (3/4). A chronological one-year/one-year split was
used to reject candidates that depended on only one regime. The selected
48-hour, EMA(200), volume-confirmed, 2-ATR, 4:1 candidate was positive in both
periods in the exploratory simulation. Because the later segment informed
candidate selection, it is a robustness check—not an untouched out-of-sample
test.

## Reproducible backtest

The repository's `WalkForwardBacktester` constructs signals from historical
prefixes only, enters at the next open, allows one position, and assumes the stop
wins when a candle touches both stop and target. The run starts with $10,000,
risks 1% of current equity per trade, charges 0.04% per side, and applies two
basis points of adverse slippage per side.

```bash
poetry run python scripts/run_solusdt_backtest.py \
  data/SOLUSDT_1h.csv --output results/solusdt_backtest.json
poetry run pytest tests/test_solusdt_strategy.py tests/test_backtesting_engine.py -q
```

| Metric | Full sample |
| --- | ---: |
| Net return | +46.84% |
| Trades | 248 |
| Win rate | 25.00% |
| Profit factor | 1.18 |
| Maximum drawdown | 19.93% |

Funding, liquidation, latency, spread, market impact, quantity/tick rounding,
taxes, and downtime are not modeled. The candle file is intentionally not
committed; retain its source, retrieval time, interval, and date range when
reproducing the study. Historical simulation is not a profit forecast.

## Promotion gate

Keep SOLUSDT out of the production signal loop until Futures-native market data
is wired through the runtime and isolated from the existing Spot candle cache.
Then keep it on testnet until a fresh, untouched period and forward test have
enough trades to evaluate profit factor, drawdown, slippage, funding, and order
reconciliation. A separate reviewed change is required for live eligibility.
