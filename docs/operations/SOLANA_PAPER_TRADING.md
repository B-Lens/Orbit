# SOLUSDT Paper Trading Runbook

## Purpose

This runbook defines how Orbit evaluates the SOLUSDT strategy before any testnet or live promotion. The SOL paper path is intentionally separate from the production order path: it consumes Binance OHLCV data, creates virtual positions, stores them in MongoDB, and never submits an exchange order.

## Strategy source and selection

The selected candidate is `SolanaMeanReversionStrategy` in `src/orbit/strategies/sol_strategy.py`.

It was ported from the dedicated SOL research implementation in the private `Orbit-Strategies` repository:

`research/producted/solana.py -> MeanReversal_BB_RSI`

That research implementation is the strongest in-repository SOL candidate because it is SOL-specific, sits in the research `producted` area, and was followed by SOL backtesting/production-oriented commits. Historical result output was not committed with the code, so Orbit does **not** treat old unverified PnL or win-rate claims as promotion evidence.

### Entry rules

The paper strategy evaluates 15-minute candles and requires all applicable filters:

- Bollinger Bands: period 20, 2 standard deviations.
- RSI: period 14; long below 30, short above 70.
- Volume: current candle volume must exceed 80% of the previous 20-candle average.
- Long location: close must be at/near the previous 10-candle low (0.5% tolerance).
- Short location: close must be at/near the previous 10-candle high (0.5% tolerance).
- Volatility filter: ATR and candle range must not be extreme relative to the recent 30-candle median.
- Risk filter: initial stop distance must be <= 1.5% of entry.

### Exit rules

- Initial stop: 1 ATR from entry.
- Initial target: 2.5R.
- Trailing stop: after a favorable close, track the best closing price and trail by 1 ATR. The new stop becomes effective on the next candle to avoid look-ahead bias.
- Profitable SMA exit: exit when price crosses 0.5% beyond SMA(20) in the profitable direction and the position is already >0.5% profitable.
- Maximum holding period: 48 hours by default.
- If SL and TP are both touched inside the same 15-minute candle, the paper engine records SL. This is deliberately conservative because the candle does not reveal which level was touched first.

## Paper account assumptions

Defaults:

| Setting | Default |
| --- | ---: |
| Symbol | SOLUSDT |
| Timeframe | 15m |
| Virtual margin per trade | 30 USDT |
| Leverage | 2x |
| Position notional | 60 USDT |
| Cooldown after exit | 2 hours |
| Maximum hold | 48 hours |
| Fee assumption | 0.0004 per side |

The old research script used zero commission and a fixed Backtrader stake. Orbit's paper runner intentionally uses fee-aware PnL and explicit virtual margin, so new paper results should be used instead of comparing raw PnL directly with the old research script.

All assumptions can be changed without touching code:

```bash
export ORBIT_SOL_PAPER_MARGIN_USDT=30
export ORBIT_SOL_PAPER_LEVERAGE=2
export ORBIT_SOL_PAPER_COOLDOWN_HOURS=2
export ORBIT_SOL_PAPER_MAX_HOLD_HOURS=48
export ORBIT_PAPER_FEE_RATE=0.0004
```

## Historical paper replay

Run a recent historical baseline before starting the forward test:

```bash
poetry run python -m orbit.paper_trading.solana --backtest-days 60
```

Recommended validation windows:

```bash
poetry run python -m orbit.paper_trading.solana --backtest-days 30
poetry run python -m orbit.paper_trading.solana --backtest-days 90
poetry run python -m orbit.paper_trading.solana --backtest-days 180
```

Do not tune parameters against only one window. If parameter changes are tested, keep a later time range untouched for out-of-sample validation.

## Forward paper trading

MongoDB is required because open/closed virtual positions and their accounting are persisted in the `paper_trades` collection.

One cycle:

```bash
poetry run python -m orbit.paper_trading.solana --once
```

Continuous 15-minute paper loop:

```bash
poetry run python -m orbit.paper_trading.solana --loop
```

The continuous process should run on the same always-on host used for Orbit. For supervised deployment, run it under systemd, Docker restart policy, or another process supervisor rather than an interactive shell.

## Statistics

```bash
poetry run python -m orbit.paper_trading.solana --stats
```

The report includes:

- closed trades
- wins and losses
- timeouts
- SMA-profit exits
- win rate
- fee-aware net PnL
- total fees
- average PnL
- average R multiple
- profit factor
- maximum drawdown

R multiples use the **initial** stop distance, even when the trailing stop later moves. This keeps risk-normalized results comparable between trades.

## Promotion gates

SOL must remain in paper mode until all of the following are satisfied.

### Gate 1: implementation validation

- Unit tests for SL/TP ambiguity, fee accounting, initial-risk R calculations, and dynamic exits pass.
- No exchange order API is reachable from `orbit.paper_trading.solana`.
- Paper documents identify the strategy version/source used for each trade.

### Gate 2: historical robustness

Use multiple non-overlapping/recent windows. A candidate should not be promoted if profitability comes from one narrow period or a handful of outlier trades.

Minimum targets for consideration, not guarantees:

- positive net PnL after fees
- profit factor >= 1.30
- average R > 0
- maximum drawdown acceptable for the planned live allocation
- sufficient trade count to avoid a tiny-sample conclusion

### Gate 3: forward paper evidence

Collect at least 50 closed forward trades; 100+ is preferred. Review:

- results across bullish, bearish, and sideways conditions
- slippage sensitivity beyond the base fee assumption
- long/short performance separately
- drawdown duration, not only drawdown size
- whether most profit depends on one or two trades

### Gate 4: Binance Futures testnet

Only after the prior gates pass should SOL be explicitly mapped to testnet:

```bash
ORBIT_ASSET_EXECUTION_MODES=SOLUSDT:testnet
```

Keep `ORBIT_LIVE_ASSETS` empty. Testnet must validate exchange filters, leverage setup, limit fills, SL/TP lifecycle, cancellation, restart recovery, and monitoring.

### Gate 5: small-capital live canary

Live requires both execution settings to agree:

```bash
ORBIT_ASSET_EXECUTION_MODES=SOLUSDT:live
ORBIT_LIVE_ASSETS=SOLUSDT
```

Start with the minimum intentionally approved allocation. Do not copy the paper leverage/margin blindly. Live sizing must satisfy Orbit's pre-trade risk policy and current Binance filters.

Stop the live canary and return to paper/testnet if execution behavior differs materially from the simulation, realized slippage invalidates expectancy, or drawdown exceeds the approved live threshold.

## Safety boundary

Adding SOL to `config/strategies.yaml` makes the strategy discoverable; it does **not** authorize exchange execution. `ExecutionSettings` defaults unmapped assets to `paper` and separately requires explicit live-asset authorization. The dedicated paper runner itself contains no order-submission calls.
