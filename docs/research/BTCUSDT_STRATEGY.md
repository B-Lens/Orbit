# BTCUSDT Strategy

BTC uses a non-repainting 1-hour breakout strategy:

- Buy above the previous 12-bar high when price is above EMA(50).
- Sell below the previous 12-bar low when price is below EMA(50).
- Use a 2.5 ATR(14) stop, 3R target, and ATR chandelier trailing stop.
- Build each hourly bar from four complete 15-minute production candles.

## Backtest comparison

Binance BTCUSDT data from 2021-01-01 through 2026-08-24 used 1% risk per
trade, next-bar-open execution, fees, slippage, and conservative stop-first
resolution.

| Period | Previous 4H return/trades | New 1H return/trades |
| --- | ---: | ---: |
| Development 70% | -16.26% / 258 | 33.22% / 507 |
| Holdout 30% | 11.50% / 85 | 22.48% / 222 |
| Full period | -6.63% / 343 | 63.31% / 729 |

The new strategy added 386 trades and improved full-period return by 69.94
percentage points. Its holdout drawdown was higher, however: 16.89% versus
9.44%. Keep it on testnet until forward results provide a meaningful sample.

## Multi-timeframe support, resistance, and liquidity study

### Research question

Discretionary traders commonly identify market structure on a higher timeframe
and execute on a lower timeframe. We tested whether mechanical versions of
that workflow improve the production breakout strategy. The experiment used
only information from completed candles and evaluated three alternatives:

1. Filter the production 1-hour breakout with matching Daily EMA(50) and
   4-hour EMA(50) regimes.
2. Detect a sweep and reclaim of the previous 48-hour high or low on 4-hour
   candles, then require a volume-confirmed 1-hour structure break.
3. Detect a 4-hour close through the previous 48-hour level, then enter only
   after a volume-confirmed 1-hour retest.

In this study, a candle crossing a prior high or low is called a *sweep
candidate*, not proof of a liquidation. True market liquidity is
multi-dimensional and is more directly measured through spread, order-book
depth, price impact, and execution speed. Bitcoin price formation is also
fragmented across independent spot and derivative venues, so one exchange's
candles cannot describe global liquidity by themselves.

Relevant background:

- Osler found that published support and resistance levels helped predict some
  intraday exchange-rate interruptions, but effectiveness varied by currency
  and source: <https://www.newyorkfed.org/medialibrary/media/research/epr/00v06n2/0007osle.pdf>
- Albers et al. document fragmentation, cross-market price impact, order-book
  imbalance information, and the importance of fees in Bitcoin markets:
  <https://arxiv.org/abs/2108.09750>
- The BIS describes spread, market depth, and price impact as distinct measures
  of liquidity: <https://www.bis.org/review/r151001a.htm>
- Apparent support and resistance can also occur in synthetic random walks,
  reinforcing the need for out-of-sample testing rather than visual selection:
  <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5552703>

### Backtest protocol

- Market: Binance spot BTCUSDT
- Data: 43,810 completed 1-hour candles from 2021-01-01 through 2025-12-31
- Development/holdout split: chronological 70/30; holdout starts 2024-07-02
- Starting equity: $10,000
- Risk: 1% of current equity per trade
- Costs: 0.04% fee and 0.02% slippage per side
- Execution: signal on a completed 1-hour candle; entry at the next open
- Collision rule: when stop and target occur in one candle, count the stop
- Position model: one position at a time
- Higher-timeframe safety: Daily information becomes available on the next day
  and 4-hour information becomes available after the 4-hour candle closes

The comparison deliberately used a small, predeclared family of rules. It did
not search a large parameter grid for a visually attractive result.

### Results

| Strategy | Development return / PF | Holdout return / PF | Full return / trades | Max drawdown |
| --- | ---: | ---: | ---: | ---: |
| Production-style 1H breakout | +25.67% / 1.07 | +36.33% / 1.20 | +66.72% / 640 | 18.29% |
| Breakout + Daily/4H EMA regime | -16.79% / 0.91 | +1.92% / 1.02 | -15.19% / 460 | 38.94% |
| 4H sweep + 1H confirmation | -20.05% / 0.69 | -14.31% / 0.59 | -31.49% / 137 | 31.49% |
| 4H breakout + 1H retest | -19.64% / 0.73 | +1.36% / 1.05 | -18.55% / 147 | 24.56% |

These figures form a separate research run from the promotion baseline above;
the periods and implementations differ, so their headline returns should not
be treated as an exact reproduction of that baseline.

### Decision and limitations

Do not replace or filter the production breakout with these candle-only
multi-timeframe rules. All three alternatives lost money over the full sample,
and the sweep model also failed the untouched holdout. The production-style
breakout remained the strongest candidate in this comparison.

The result does **not** show that higher-timeframe analysis or liquidity data
are useless. It shows that candle wicks, rolling levels, and EMA agreement are
insufficient proxies. A future experiment should retain the breakout baseline
and predeclare filters using versioned historical futures data, including open
interest, funding, taker buy/sell imbalance, liquidation events, and preferably
cross-venue order-book depth. Such a study must model data latency, fees,
slippage, and unavailable fills before it can influence Testnet execution.
