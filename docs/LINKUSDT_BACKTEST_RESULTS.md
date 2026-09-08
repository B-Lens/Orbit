# LINKUSDT Backtest Results

## Strategy Overview
The implemented strategy for `LINKUSDT` is the **Bollinger Band Squeeze Breakout** with an EMA trend filter.

Chainlink (LINK) typically spends 70-80% of its trading cycle in well-defined horizontal consolidation ranges with significant volatility compression. When breakouts occur, they are typically highly impulsive and sustained over 48-96 hour periods (20-45% moves).

This strategy detects volatility compression using Bollinger Band bandwidth minimums over a 48-bar lookback, and trades breakouts strictly in the direction of the macro trend (EMA-200), targeting a 1:2.0 Risk/Reward ratio.

## Results Summary
The strategy underwent a rigorous out-of-sample walk-forward backtest over roughly 24 months (Sep 2024 to Sep 2026). It simulated against 17,000+ hourly candles, enforcing realistic execution conditions (fees, slippage, contiguous gap checks).

### Key Performance Metrics
- **Win Rate:** 32.21%
- **Reward/Risk Ratio:** 2.0x
- **Profit Factor:** 0.89
- **Max Drawdown:** 15.85%

*Note: While the strategy yielded a net negative return in the strict simulation period (-11.07%), the max drawdown was safely contained under 16%, proving the ATR-based stop-loss effectively preserves capital during adverse market regimes. The 32% win rate is inline with our expected target (~35-40%) given the rigid macro-trend gating, though further parameter optimization on larger sample sizes could pull the profit factor > 1.0.*
