# LINKUSDT strategy research and testnet forward validation

## Decision

LINKUSDT is registered for **testnet-only forward validation**. It is not
authorized for live execution. The best full-sample candidate in this study was
profitable after modeled costs, but failed the predeclared chronological robustness
check. Calling it a proven profitable strategy would overstate the evidence.

`LINKUSDTStrategy` is registered in `config/strategies.yaml` with
`execution_mode: testnet`. Testnet activity is intended to measure live-like signal,
fill, and order-lifecycle behavior; it does not validate or authorize live trading.

## Research basis and candidate

The study evaluated a modest, predeclared Donchian-breakout family: 24/48/72-hour
lookbacks, EMA(100/200), volume thresholds of 1.0/1.2/1.4 times the preceding
24-hour average, 1.5/2.0 ATR(14) stops, and 2:1/3:1/4:1 initial reward/risk.
The rules use only completed hourly candles:

- buy above the preceding 72-hour high, above EMA(200), with volume above 1.4×
  the preceding 24-hour mean;
- sell below the preceding 72-hour low, below EMA(200), with the same volume gate;
- stop at 2× ATR(14), target at 3× initial risk, with only one open position.

This is a plausible trend-following hypothesis, not an asserted LINK-specific
market fact. Research on cryptocurrencies has found that breakout and trend rules
can have predictive content, especially in trending regimes, but that is not proof
of a persistent LINK edge. [Technical trading and cryptocurrencies](https://doi.org/10.1007/s10479-019-03357-1)
and [Bitcoin trading-rule evidence](https://doi.org/10.1016/j.frl.2019.08.011)
motivate testing the family. The hourly Binance USD-M Futures kline endpoint is
the data source; its interval and pagination contract is documented in the
[Binance API reference](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Kline-Candlestick-Data).

## Reproducible backtest

The data file is intentionally not committed. Download 17,520 contiguous
LINKUSDT USD-M perpetual 1-hour candles from Binance for 2024-09-08 00:00 UTC
through 2026-09-07 23:00 UTC, preserving `timestamp,open,high,low,close,volume`.

```bash
poetry run python scripts/run_linkusdt_backtest.py \
  data/LINKUSDT_1h.csv --output results/linkusdt_research_backtest.json
poetry run pytest tests/test_linkusdt_strategy.py tests/test_backtesting_engine.py -q
```

`WalkForwardBacktester` builds every signal from a historical prefix, enters at
the next hourly open, permits one position, takes the stop when a bar reaches stop
and target, risks 1% of current equity, charges 0.04% per side, and applies two
basis points of adverse slippage per side. Funding, spread, market impact,
quantity/tick rounding, latency, taxes, and outages are not modeled.

| Segment | UTC period | Candles | Return | Trades | Profit factor | Maximum drawdown |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Full sample | 2024-09-08 00:00 to 2026-09-07 23:00 | 17,520 | +26.33% | 233 | 1.13 | 22.93% |
| First chronological half | 2024-09-08 00:00 to 2025-09-07 23:00 | 8,760 | +40.15% | 119 | 1.43 | 12.84% |
| Second chronological half | 2025-09-08 00:00 to 2026-09-07 23:00 | 8,760 | **−5.98%** | 110 | **0.93** | 17.67% |

"Segment" means an equal, contiguous, non-overlapping half of the same candle
history—not a trade type or a live deployment phase. The split is a robustness
screen, not an untouched out-of-sample test, because the same sample informed
selection. More importantly, all 108 combinations in the predeclared grid lost
money in the second half; the least-negative result was −5.70%. The aggregate
result therefore does not meet the project’s promotion bar.

## Forward-test and live-promotion gate

Keep LINKUSDT in testnet mode. Before a separate live-promotion proposal, collect
enough forward-test trades to assess profit factor, drawdown, slippage, funding,
and order reconciliation, and repeat validation on a separately held-out period
with Futures-native candles and spread/impact estimates. A negative forward result,
or a material order-lifecycle safety issue, requires disabling this testnet strategy.
