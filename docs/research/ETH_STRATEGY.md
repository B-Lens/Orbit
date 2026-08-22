# ETHUSDT strategy promotion dossier

## Ownership and status

- Implementation: `src/orbit/strategies/eth_strategy.py` (`ETHStrategy`)
- Registry: `config/strategies.yaml`
- Timeframe: 1 hour; complete 15-minute candles are resampled internally
- Current eligibility: **Testnet only**
- Promotion rule: do not change `execution_mode` from `testnet` until the criteria below pass

## Signal contract

The strategy requires at least 200 hourly bars and above-average volume
(`volume > 1.2 × SMA(20)`). It combines:

- EMA 21/55 crossover or pullback within `0.5 × ATR(14)`
- EMA 200 directional regime filter
- RSI 14 bands: 40–65 for buys, 35–60 for sells
- MACD 12/26/9 histogram confirmation
- stop loss at `2 × ATR`; take profit at `3 × ATR` (1.5:1 reward/risk)

## Baseline evidence

The committed walk-forward result in `results/eth_backtest.json` was generated
on 1-hour data from September 2025 to August 2026 with $10,000 starting equity
and 1% backtest risk per trade.

| Metric | Baseline |
| --- | ---: |
| Net return | +4.39% |
| Trades | 118 |
| Win rate | 44.92% |
| Profit factor | 1.06 |
| Maximum drawdown | 10.03% |

This is a historical baseline, not evidence of future profitability. Re-run it
against fresh, versioned data before comparing Testnet or live performance.

```bash
poetry run python scripts/run_eth_backtest.py --timeframe 1h --equity 10000 --risk 0.01
poetry run pytest tests/test_eth_strategy.py tests/test_backtesting_engine.py
```

## Promotion checklist

Before live eligibility, record the evaluated commit and dataset, then require:

1. Deterministic strategy and backtester tests pass.
2. Fresh out-of-sample results remain profitable after fees and slippage.
3. Testnet reaches a predeclared minimum trade count across multiple regimes.
4. Testnet profit factor, drawdown, slippage, and order-reconciliation limits pass.
5. Stop/target placement, incomplete-candle exclusion, and restart recovery are verified.
6. Any future live-mode work requires a separately reviewed configuration and
   authorization design; the current runtime rejects it.

Record each evaluation in the promoting PR; never replace failed evidence with
an unversioned result.
