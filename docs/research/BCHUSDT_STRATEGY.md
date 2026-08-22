# BCHUSDT strategy promotion dossier

## Ownership and status

- Implementation: `src/orbit/strategies/bch_strategy.py` (`BCHUSDTStrategy`)
- Registry: `config/strategies.yaml`
- Timeframe: 4 hours; 15-minute production candles are resampled internally
- Current eligibility: **Testnet only**
- Promotion rule: do not change `execution_mode` from `testnet` until the criteria below pass

## Market context and research rationale

BCH/USDT exhibits:

- High volatility relative to BTC, with magnified trend phases
- Regular regime alternation between sustained directional runs and choppy consolidation
- A tendency for momentum to persist on 4-hour candles more reliably than on 1-hour candles

Research considered three candidate approaches:
1. **1H momentum (EMA crossover + RSI + MACD)** — too many whipsaws on the hourly timeframe; produced –30.5 % ROI over 12 months in walk-forward tests.
2. **4H Bollinger-Band reversal** (the previous `BollingerAdaptiveReversalStrategyBCH`) — positive ROI in short windows but sensitive to band width and reversal pattern quality.
3. **4H Trend-Momentum with ADX regime gate** ← **chosen** — the ADX filter removes low-confidence consolidation phases, allowing trend-following entries with a >2 : 1 reward/risk ratio that maintains positive expectancy at low win rates.

## Signal contract

The strategy requires at least 210 four-hour bars (~35 days). The ADX gate
(`ADX(14) > 18`) must pass before any other condition is evaluated.

| Component | Parameter | Purpose |
|-----------|-----------|---------|
| EMA 20 / 50 | crossover or pullback ≤ 0.5 × ATR within 5 bars | Entry timing |
| EMA 200 | macro regime filter | Bull/bear context |
| RSI 14 | BUY: 38–70 · SELL: 30–62 | Momentum confirmation |
| MACD 12/26/9 | histogram rising (BUY) / falling (SELL) or correct sign | Secondary momentum gate |
| Volume SMA 20 | current volume > 1.1 × SMA | Liquidity confirmation |
| ATR 14 | stop loss 1.8 × ATR; take profit 4.0 × ATR | Dynamic risk sizing (≈2.22 : 1 R:R) |

## Baseline evidence

Walk-forward backtest on live Binance 4-hour OHLCV data:

| Field | Value |
|-------|-------|
| Dataset | BCHUSDT 4h, Aug 2025 – Aug 2026 (8 760 raw bars → 2 190 4-hour bars) |
| Starting equity | $10 000 |
| Risk per trade | 1 % |
| Fee rate | 0.04 % round-trip |
| Slippage | 2 bps |

| Metric | Baseline |
|--------|-------:|
| Net return | **+0.80 %** |
| Net PnL | +$79.82 |
| Trades | 30 |
| Win rate | 33.33 % |
| Profit factor | **1.04** |
| Max drawdown | 4.09 % |
| Profit/Loss ratio | 2.08 |
| CAGR | +0.80 % |
| Sharpe ratio | 0.14 |
| Sortino ratio | 16.27 |
| Calmar ratio | 0.20 |

> **Note:** This is a historical baseline on a 12-month window, not evidence of future
> profitability. The profit factor of 1.04 leaves very little margin for adverse market
> regime shifts. Re-run against fresh, versioned data before making promotion decisions.

```bash
poetry run python scripts/run_bch_backtest.py --equity 10000 --risk 0.01 --days 365
poetry run pytest tests/test_bch_strategy.py -v
```

## Promotion checklist

Before live eligibility, record the evaluated commit and dataset, then require:

1. Deterministic strategy and backtester tests all pass (`tests/test_bch_strategy.py`).
2. Fresh out-of-sample results remain profitable after fees and slippage on an
   independently sourced dataset.
3. Testnet reaches a minimum of 20 completed trades across at least two distinct
   market regimes (trending and consolidating).
4. Testnet profit factor ≥ 1.05, max drawdown ≤ 15 %, and order-reconciliation
   checks pass.
5. Stop/target placement, incomplete-candle exclusion, and restart recovery verified
   on Futures Testnet.
6. ADX threshold sensitivity verified: confirm that lowering to 15 or raising to 22
   does not significantly improve or destroy performance (guards against parameter
   fragility).
7. Any future live-mode work requires a separately reviewed configuration and
   authorization design; the current runtime rejects it.

Record each evaluation in the promoting PR; never replace failed evidence with an
unversioned result.
