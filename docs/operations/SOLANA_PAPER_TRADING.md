# SOLUSDT web-researched paper trading

This runbook covers the paper-only `SolanaVolatilityMomentumStrategy`. The
strategy was designed independently from Orbit's existing strategies and has no
order-submission dependency. SOLUSDT must remain unmapped from testnet/live
execution while the evidence below is negative.

## Research basis

The candidate translates the following public research into deliberately simple,
auditable rules:

- Cryptocurrency time-series momentum has stronger evidence than
  cross-sectional momentum, but liquidation, skew, and fat tails make raw mean
  returns unreliable: <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4675565>
- Volatility scaling can improve momentum strategies, although it does not help
  every strategy or regime: <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5090097>
- Recent multi-asset crypto research evaluates trend following on six-hour bars
  with volatility-aware trailing stops: <https://arxiv.org/abs/2602.11708>
- Volume-weighted time-series momentum has empirical support in cryptocurrency
  markets: <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4825389>

These sources motivate a hypothesis, not a claim of profitability.

## Candidate rules

Signals are evaluated only on completed six-hour UTC bars aggregated from
15-minute SOLUSDT candles.

- Long: close above 8 EMA, 8 EMA above 24 EMA and rising, RSI(14) from 52 to 72.
- Short: close below 8 EMA, 8 EMA below 24 EMA and falling, RSI(14) from 28 to 48.
- Confirmation: six-hour volume is at least its 20-period median.
- Volatility gate: ATR(14) is between 0.3% and 6% of price.
- Initial stop: 2 ATR; target: 3 ATR.
- Exit: hard stop/target, six-hour momentum reversal, trailing stop, or 48-hour timeout.
- Simulation: 30 USDT virtual margin, 2x notional, and 4 bps per side by default.

The paper engine conservatively records the stop when a candle touches both the
stop and target because OHLCV does not reveal intrabar ordering.

## Current evidence: rejected

On 2026-08-17, the fixed rules were replayed on Binance public SOLUSDT spot
15-minute candles with simulated fees:

| Window | Trades | Win rate | Net PnL | Profit factor | Max drawdown |
| --- | ---: | ---: | ---: | ---: | ---: |
| 30 days | 11 | 9.09% | -4.41 USDT | 0.355 | 4.64 USDT |
| 90 days | 32 | 40.63% | -0.78 USDT | 0.956 | 8.60 USDT |
| 180 days | 75 | 33.33% | -21.52 USDT | 0.646 | 28.35 USDT |

The candidate fails every tested window after fees. Do not start forward paper
state, promote it to Binance Futures Testnet, or authorize it for live execution.
Future research should use a new hypothesis and untouched out-of-sample window,
not parameter tuning against these results.

## Commands

Historical replay (public market data, virtual trades only):

```bash
poetry run python -m orbit.paper_trading.solana --backtest-days 90
```

Forward paper execution requires MongoDB to persist virtual positions:

```bash
poetry run python -m orbit.paper_trading.solana --once
poetry run python -m orbit.paper_trading.solana --loop
poetry run python -m orbit.paper_trading.solana --stats
```

Do not run forward mode for the currently rejected candidate. The forward runner
does not call an exchange order API, but persisting trades for a failed strategy
would not create useful promotion evidence.
