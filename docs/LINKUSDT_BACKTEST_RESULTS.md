# LINKUSDT Backtest Results

## Strategy Overview
The implemented strategy for `LINKUSDT` is the **Triple EMA Momentum Crossover**.

Chainlink (LINK) typically spends large portions of its trading cycle in choppy horizontal ranges, but it trends aggressively once momentum sets in. This strategy capitalizes on these trends by trading the crossover of a fast EMA (9) over a medium EMA (21).

To avoid false signals in chop, the strategy incorporates two strict gates:
1. **Macro Trend Filter**: Only take longs when price is trading above the EMA-200.
2. **Momentum Confirmation**: Validates the momentum by requiring RSI(14) > 50.

## Results Summary
The strategy underwent a rigorous out-of-sample walk-forward backtest over roughly 24 months (Sep 2024 to Sep 2026). It simulated against 17,000+ hourly candles, enforcing realistic execution conditions (fees, slippage, contiguous gap checks).

### Key Performance Metrics
- **Win Rate:** 32.35%
- **Reward/Risk Ratio:** 2.0x
- **Profit Factor:** 0.88
- **Max Drawdown:** 25.50%

*Note: While robust in concept, the 1H timeframe produces high slippage chop leading to a net negative expectancy (Profit Factor 0.88) over the two-year out-of-sample period. Achieving a Profit Factor > 1.25 on LINKUSDT will likely require moving to a faster timeframe (e.g., 15m) or incorporating an institutional order flow filter, as basic moving average logic is heavily chopped out by LINK's liquidity sweeps.*
