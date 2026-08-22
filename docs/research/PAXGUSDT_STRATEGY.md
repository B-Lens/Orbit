# PAXGUSDT strategy promotion dossier

## Ownership and status

- Implementation: `src/orbit/strategies/paxgusdt_strategy.py` (`PAXGUSDTStrategy`)
- Registry: `config/strategies.yaml`
- Timeframe: 15 minutes; active candles are excluded
- Current eligibility: **Paper and Testnet only**
- Promotion rule: do not change `execution_mode` from `testnet` until the criteria below pass

## Signal contract

The strategy trades a close outside the previous 48-bar Donchian channel
(12 hours) when there is no existing position. Risk levels use ATR(14):

- long: close above the prior channel high
- short: close below the prior channel low
- stop loss: `2 × ATR`
- take profit: `6 × ATR` (3:1 reward/risk)

Configuration currently uses a $100 fixed allocation, symbol risk value `0.01`,
2-hour cooldown, and quantity precision of two decimals. Global risk policy and
exchange filters remain authoritative and can reduce or reject the requested size.

## Baseline evidence

The original 60-day, 15-minute research run reported the following point-in-time
results. The dataset and generated report are not committed, so these figures
must be reproduced before they are used for promotion.

| Metric | Reported baseline |
| --- | ---: |
| Net P&L | +$621.49 |
| Win rate | 35.21% |
| Profit factor | 1.11 |
| Maximum drawdown | 7.17% |

The low win rate depends on preserving the 3:1 payout profile. Small changes in
fees, slippage, or stop execution may remove the reported edge.

## Promotion checklist

Before live eligibility, record the evaluated commit and dataset, then require:

1. Add deterministic tests for long, short, active-candle, existing-position,
   insufficient-history, and 3:1 reward/risk behavior.
2. Commit a reproducible walk-forward command or script and fresh out-of-sample report.
3. Confirm PAXG market-data and cached state remain isolated by execution environment.
4. Testnet reaches a predeclared minimum trade count across breakout and range regimes.
5. Testnet profit factor, drawdown, slippage, and order-reconciliation limits pass.
6. Verify stop/target lifecycle and restart recovery on Futures Testnet.
7. Any future live-mode work requires a separately reviewed configuration and
   authorization design; the current runtime rejects it.

Record each evaluation in the promoting PR; never treat this baseline as a live
profit guarantee.
