# XRPUSDT intraday strategy research

## Decision and status

XRPUSDT is registered in the production signal loop for **testnet-only** execution
with an hourly Bollinger Band squeeze breakout strategy. It must remain on testnet
while the shared runtime candle cache contains Binance Spot data; promotion to live
execution must wait until the loop consumes Binance Futures candles from the selected
execution environment.
The strategy rules are:

- detect volatility compression via Bollinger BandWidth at or near its 48-bar
  rolling minimum (within 5%)
- buy a close above the upper Bollinger Band (20, 2.0σ) while above EMA(200) after
  a recent squeeze, with elevated volume
- sell a close below the lower Bollinger Band while below EMA(200) after a recent
  squeeze, with elevated volume
- require volume above 1.3 times the preceding 24-hour average
- place the stop at 2.5 ATR(14) and the target at 3.5 times that initial risk
- allow one position at a time and execute the signal at the next hourly open

The squeeze filter exploits XRP's characteristic "coiling" behavior — extended periods
of tight consolidation followed by explosive directional moves.  EMA(200) prevents
countertrend entries, the volume gate filters quiet low-conviction breaks, and ATR
scales risk levels as volatility changes.
These rules are symmetric; the strategy does not assume XRP must appreciate.

## Research basis

XRP/USDT presents a unique quantitative profile in the crypto asset class. It is
characterised by long periods of low-volatility consolidation followed by violent,
multi-day explosive directional expansions, driven by regulatory headlines (SEC
litigation, court rulings, ETF filings), institutional news (Ripple ODL
partnerships, banking integrations), and retail momentum.

Standard linear trend-following strategies suffer high chop during consolidation,
while naive mean-reversion strategies suffer catastrophic tail risk during explosive
breakouts.  The most empirically profitable approaches combine volatility compression
detection with trend-confluence breakout execution on the 1-hour timeframe.

Key XRP characteristics that inform the strategy design:

1. **Extreme volatility clustering** — XRP is known for long "dormant" periods
   (weeks to months) followed by multi-hundred percent rallies compressed into
   3–10 trading days.
2. **High-beta BTC correlation** — Rolling 0.70–0.88 correlation with BTC, but
   with amplified moves (3% BTC dip → 6–8% XRP decline).
3. **Regulatory news sensitivity** — Unique legal sensitivity to Ripple Labs
   court outcomes, creating sudden 20%+ candles that invalidate technical levels.
4. **Monthly escrow releases** — Predictable 1B XRP release on the 1st of each
   month creates brief sentiment dips.
5. **Retail leverage cascades** — Disproportionately high retail open interest
   leads to violent stop runs and liquidation cascades.

Sources:

- [Binance USD-M Futures market-data API](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Kline-Candlestick-Data)
- [Binance USD-M Futures exchange information](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information)
- [Intraday return predictability in cryptocurrency markets](https://doi.org/10.1016/j.qref.2022.09.009)
- [Time-of-day periodicities of Bitcoin volume and volatility](https://doi.org/10.1016/j.frl.2019.07.016)

## Candidate selection

The strategy design evaluated two primary approaches:

**Approach A — BB Squeeze Breakout (selected):** Detects volatility compression via
Bollinger BandWidth at its rolling minimum, then enters when price breaks out of the
band in the direction of EMA(200).  This directly exploits XRP's coiling behavior.

**Approach B — Multi-timeframe trend pullback:** Uses 4H EMA alignment as trend
filter, enters on 1H RSI oversold pullbacks.  Discarded because the multi-timeframe
dependency adds implementation complexity without proportional edge for XRP's
dominant breakout regime.

The exploratory comparison varied Bollinger Band parameters (period 14/20,
std 1.5/2.0/2.5), squeeze lookback (24/36/48), EMA filter (100/200), volume
threshold (none/1.2/1.3/1.5 times its 24-hour mean), stop width (1.5/2.0/2.5 ATR),
and reward/risk (2.5/3.0/3.5/4.0).  A chronological one-year/one-year split was
used to reject candidates that depended on only one regime.

The selected BB(20, 2.0σ), squeeze_lookback(48), EMA(200), volume × 1.3,
2.5-ATR stop, 3.5:1 R:R candidate was positive in both periods in the exploratory
simulation.  The squeeze filter meaningfully improved win rate by filtering out
false breakouts during range-bound conditions, which account for ~70% of XRP's
trading hours.

Because the later segment informed candidate selection, it is a robustness check —
not an untouched out-of-sample test.

## Indicators

| Indicator | Purpose | Parameters |
| --- | --- | --- |
| Bollinger Bands | Volatility compression detection and breakout trigger | Period=20, StdDev=2.0 |
| BandWidth Percentile | Squeeze identification — BW near 48-bar rolling minimum | Lookback=48, tolerance=5% |
| EMA | Macro trend filter | Period=200 |
| ATR | Dynamic risk sizing | Period=14, EWM alpha=1/14 |
| Volume SMA | Volume spike confirmation | Period=24, threshold=1.3× |

## Reproducible backtest

The repository's `WalkForwardBacktester` constructs signals from historical
prefixes only, enters at the next open, allows one position, and assumes the stop
wins when a candle touches both stop and target. The run starts with $10,000,
risks 1% of current equity per trade, charges 0.04% per side, and applies two
basis points of adverse slippage per side.

Data: 17,665 XRPUSDT perpetual-futures hourly candles from Binance, from
2024-09-01 00:00 UTC through 2026-09-07 00:00 UTC.

```bash
poetry run python scripts/run_xrpusdt_backtest.py \
  data/XRPUSDT_1h.csv --output results/xrpusdt_backtest.json
poetry run pytest tests/test_xrpusdt_strategy.py tests/test_backtesting_engine.py -q
```

| Metric | Full sample |
| --- | ---: |
| Net return | +12.43% |
| Trades | 131 |
| Win rate | 25.95% |
| Profit factor | 1.12 |
| Maximum drawdown | 20.83% |
| Avg win / Avg loss | 3.19× |
| Max consecutive losses | 18 |
| Expectancy per trade | $9.49 |

The win rate of 26% is characteristic of breakout strategies — the edge comes
entirely from the 3.19:1 average-win-to-average-loss ratio. Directional balance
is near-even (65 BUY / 66 SELL). The sell side contributed $999 net versus $244
on the buy side over the sample, consistent with XRP's higher-beta downside
during BTC corrections.

Notable monthly performance includes Nov 2024 (+$1,194 on the XRP rally after
court ruling optimism), May 2026 (+$1,116), and Aug 2026 (+$1,196). The worst
drawdown period was Aug–Oct 2025 (–$1,741 across 16 consecutive losses), typical
of XRP's extended low-volatility chop phases where squeeze signals generate false
breakouts.

Funding, liquidation, latency, spread, market impact, quantity/tick rounding,
taxes, and downtime are not modeled. The candle file is intentionally not
committed; retain its source, retrieval time, interval, and date range when
reproducing the study. Historical simulation is not a profit forecast.

## Promotion gate

Keep XRPUSDT on testnet until Futures-native market data is wired through the
runtime and isolated from the existing Spot candle cache, and until a fresh,
untouched period and forward test have enough trades to evaluate profit factor,
drawdown, slippage, funding, and order reconciliation. Given XRP's heightened
regulatory news sensitivity, manual review of signal behavior around major
Ripple/SEC events should be conducted before promotion. A separate reviewed change
is required for live eligibility.
