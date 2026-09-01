# MKRUSDT intraday strategy research

## Decision

The strongest robust candidate tested was an hourly trend-following breakout:

- buy above the previous 24-hour high when price is above EMA(100)
- sell below the previous 24-hour low when price is below EMA(100)
- stop at 1.5 ATR(14), target at four times the initial risk
- enter at the next hourly open; permit one position at a time

The strategy was positive in development, validation, and untouched holdout data.
It is **research-only and not registered for production**. Binance MKRUSDT
Futures data stops in September 2025 following the MKR-to-SKY market migration,
so this result does not establish that an executable MKRUSDT market exists now.

## Method

The study used Binance USD-M Futures hourly candles from 2021-01-01 through
2025-09-15. A chronological 60/20/20 split prevented holdout information from
being used for parameter selection. The search compared Donchian lookbacks,
EMA trend filters, ATR stop widths, and reward/risk ratios. The selected setup
favored consistent positive results across the first two segments rather than
the single highest validation return.

The simulator starts with $10,000, risks 1% of current equity per trade, charges
0.04% at entry and exit, and applies two basis points of adverse slippage at
entry and exit. Signals use completed candles and execute at the next open.
When stop and target occur in one candle, the stop wins. Funding, liquidation,
market impact, taxes, and exchange quantity constraints are not modeled.

## Results

| Segment | Approximate period | Return | Trades | Win rate | Profit factor | Max drawdown |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Development | 2021-01-01–2023-10-28 | +64.37% | 588 | 23.47% | 1.10 | 27.74% |
| Validation | 2023-10-28–2024-10-06 | +19.93% | 181 | 23.76% | 1.14 | 20.54% |
| Holdout | 2024-10-06–2025-09-15 | **+37.36%** | 202 | 24.75% | 1.18 | 14.18% |

These are historical simulated returns, not a profit forecast. The low win rate
is intentional: profitability depends on retaining the 4:1 payoff, and higher
real-world costs can erase the modest profit-factor edge.

## Reproduction and operational status

Run `python3 scripts/run_mkr_backtest.py`. The command downloads and caches the
fixed historical range in `data/MKRUSDT_1h.csv`, then writes the detailed report
to ignored path `results/mkr_backtest.json`.

Before adapting this work to SKYUSDT, perform a new study on native SKY data,
include funding and exchange filters, and require paper/testnet forward results.
Do not transfer MKR price parameters mechanically across the 1:24,000 token
conversion or enable this class in `config/strategies.yaml`.
