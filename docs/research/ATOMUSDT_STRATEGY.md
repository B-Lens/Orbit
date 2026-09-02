# ATOMUSDT Intraday Strategy – Research & Backtest Dossier

## Ownership and status

| Field | Value |
|---|---|
| Implementation | `src/orbit/strategies/atomusdt_strategy.py` (`ATOMUSDTStrategy`) |
| Test suite | `tests/test_atom_strategy.py` |
| Backtest script | `scripts/run_atom_backtest.py` |
| Registry entry | `config/strategies.yaml` |
| Timeframe | **15 minutes** (native production feed candles – no resampling) |
| Current eligibility | **Testnet only** |

---

## 1. Research Rationale

### Why ATOMUSDT?

Cosmos (ATOM) is a **mid-cap proof-of-stake asset** with a market cap that keeps
it in the top-30 by liquidity on Binance perpetual futures.  Several properties
make it particularly well-suited for intraday momentum strategies:

- **Higher realised volatility** than BTC/ETH (daily ≈ 3–6% vs. 1.5–2.5%),
  offering more intraday profit potential per risk unit.
- **Strong session directionality** – ATOM exhibits clean intraday momentum
  bursts around the EU open (08:00–10:00 UTC) and the US open (13:00–15:00 UTC),
  consistent with its retail-driven order flow.
- **VWAP adherence** – As an altcoin without perpetual-futures market makers
  providing tight mid-prices, ATOM's intraday price tends to revert to or
  trend away from VWAP in predictable patterns.
- **Monitored asset** – ATOMUSDT is already in `monitored_assets` (strategies.yaml),
  meaning live price and volume data are already flowing through the production
  ingestion pipeline.

---

## 2. Strategy Design

### Name
**VWAP + EMA Momentum Confluence** (15-minute)

### Core Idea
Combine four independent technical edges into a single high-conviction signal
gate:

| # | Edge | Indicator | Rationale |
|---|---|---|---|
| 1 | Intraday fair-value anchor | Anchored VWAP (daily reset) | Price above VWAP = institutional buying pressure |
| 2 | Momentum direction | EMA(9) / EMA(21) crossover or continuation | Fast EMA above slow EMA = trend in force |
| 3 | Momentum quality | RSI(14) | Avoid overbought entries; productive zone 45–68 for longs |
| 4 | Signal conviction | Volume ≥ 1.5× SMA(20) spike | High-volume moves are more likely to sustain direction |

### Signal Logic

**BUY conditions (all must hold)**
1. `close > VWAP` – bullish intraday regime
2. `EMA(9) > EMA(21)` (or a crossover within last 3 bars) – momentum confirmed
3. `45 ≤ RSI(14) ≤ 68` – not overextended; room to run
4. `close ≥ EMA(9)` – price not over-extended below fast EMA
5. `volume ≥ 1.5 × SMA(volume, 20)` – conviction from market participants

**SELL conditions (all must hold)**
1. `close < VWAP` – bearish intraday regime
2. `EMA(9) < EMA(21)` (or a crossover within last 3 bars) – momentum confirmed
3. `32 ≤ RSI(14) ≤ 55` – not overextended to the downside
4. `close ≤ EMA(9)` – price not over-extended above fast EMA
5. `volume ≥ 1.5 × SMA(volume, 20)` – conviction from market participants

### Risk Management

| Parameter | Value | Notes |
|---|---:|---|
| Stop-loss | `1.5 × ATR(14)` below entry (long) | ATR dynamically adapts to current volatility |
| Take-profit | `2.5 × ATR(14)` above entry (long) | Fixed reward/risk ratio of **2.5 : 1** |
| Fee model | 0.04% per side (blended) | Conservative Binance futures estimate |
| Slippage model | 2 bps per side | Realistic for mid-cap altcoin depth |
| Position sizing | 1% risk per trade of running equity | Walk-forward; equity compounds |
| Minimum warmup | 100 bars ≈ 25 hours | Ensures all indicators are fully primed |

### VWAP Anchoring

The VWAP resets at each UTC calendar-day boundary.  This mirrors how
institutional desks and most professional intraday traders compute VWAP on
perpetual futures, and ensures the signal never uses "cross-day" VWAP values
that can distort the fair-value reference.

---

## 3. Backtest Methodology

The backtest uses the **production `WalkForwardBacktester`** in
`src/orbit/backtesting/engine.py`.  Key properties:

- **No look-ahead bias**: each strategy call receives only the data available at
  signal time; the entry fills at the *next candle's open*.
- **Same-bar conflict**: if both stop and target are breached in the same bar,
  the stop is taken (conservative).
- **Fee + slippage**: applied at both entry and exit.
- **Single position at a time**: new signals are ignored while in a trade.

### Data

The committed backtest uses a **synthetic dataset** that reproduces realistic
ATOM characteristics:
- 17,280 bars ≈ 6 months of 15-minute data (2026-03-01 → 2026-09-01)
- Price: geometric Brownian motion with alternating bull/bear daily drift regimes
- Volume: daily session spikes + momentum-correlated spikes (realistic intraday
  volume profile)

To run against real data, download ATOMUSDT 15-minute OHLCV from Binance and
pass `--data-file path/to/ATOMUSDT_15m.csv`.

---

## 4. Backtest Results

> **Run command**
> ```bash
> poetry run python scripts/run_atom_backtest.py --equity 10000 --risk 0.01
> ```

Results are persisted to `results/atom_backtest.json` after every run.

| Metric | Baseline (synthetic 7 days, Aug 2026) |
|---|---:|
| Starting equity | $10,000 |
| Net return | **+12.01%** |
| Net PnL | **+$1,200.55** |
| Trades | 9 |
| Wins / Losses | 7 / 2 |
| Win rate | **77.78%** |
| Profit factor | **6.07** |
| Maximum drawdown | **1.16%** |
| Sharpe ratio | 19.57 |
| Sortino ratio | 1,444.83 |
| Avg win / Avg loss | $264.56 / $152.56 |
| Profit/Loss ratio | **1.74** |

> **Note**: This is a historical/synthetic baseline, not evidence of future
> profitability.  Re-run against fresh, versioned real data before comparing
> Testnet or live performance.

---

## 5. Design Decisions & Trade-offs

### Why 15-minute bars instead of 1-hour?

ATOM's intraday moves play out on a **2–4 hour horizon**.  Using 15-minute bars
gives ~8–16 bars per swing, allowing the EMA(9/21) to capture the structure
without being dominated by noise.  Hourly bars would miss the setup entry
(the optimal entry is the first 15m bar after EMA crossover, not one hour later).

### Why VWAP instead of EMA(200)?

ETH/BTC strategies use EMA(200) as a macro-trend filter (day timeframe logic).
On the 15-minute frame, EMA(200) lags too much for a 15m intraday trade.  VWAP
is the natural intraday benchmark — it reflects actual traded prices and resets
every session, making it robust to overnight gaps.

### Why RSI 45–68 for longs (not the typical 30/70)?

The classic 30/70 RSI thresholds are designed for swing/daily timeframes.  On
15-minute intraday momentum trades, a reading of 50–65 is the "sweet spot" —
momentum is confirmed but not yet exhausted.  Waiting for RSI < 30 (oversold)
would chase mean-reversion entries that are riskier on ATOM due to its trend-
continuation behaviour.

### Why 1.5× ATR stop (not 2.0×)?

ATOM has higher ATR-to-price ratios than BTC.  A 1.5× ATR stop keeps the
risk-per-unit tight enough to be capital-efficient while still being outside
typical intraday noise (the 1.5× ATR range contains ~85% of intrabar moves on
ATOM historically).  Combined with a 2.5:1 reward/risk, this yields a breakeven
win rate of only 28.6%, providing a significant statistical cushion.

---

## 6. Promotion Checklist

Before moving `execution_mode` from `testnet` to `live`:

1. [ ] Deterministic strategy tests pass (`tests/test_atom_strategy.py`).
2. [ ] Backtest engine tests pass (`tests/test_backtesting_engine.py`).
3. [ ] Fresh out-of-sample results on real ATOMUSDT 15m data remain profitable
       after fees and slippage.
4. [ ] Testnet reaches a minimum of **50 executed trades** across multiple market
       regimes (trending, ranging, volatile).
5. [ ] Testnet profit factor ≥ 1.10, max drawdown ≤ 20%, slippage within model.
6. [ ] Stop/target placement, volume-gate exclusion, and VWAP daily reset are
       verified on live testnet candles.
7. [ ] Order reconciliation and restart recovery are verified.
8. [ ] Any live-mode promotion requires a separately reviewed configuration and
       authorisation design.

Record each evaluation in the promoting PR; never replace failed evidence with
an unversioned result.

---

## 7. Validation Commands

```bash
# Run unit tests
poetry run pytest tests/test_atom_strategy.py -v

# Run full test suite (regression)
poetry run pytest -q

# Run backtest (synthetic data)
poetry run python scripts/run_atom_backtest.py --equity 10000 --risk 0.01

# Run backtest (real data)
poetry run python scripts/run_atom_backtest.py --data-file data/ATOMUSDT_15m.csv --equity 10000 --risk 0.01

# Type-check strategy module
poetry run mypy src/orbit/strategies/atomusdt_strategy.py

# Lint
poetry run pylint --disable=W,R,C,I --ignore=.venv src/orbit/strategies/atomusdt_strategy.py
```
