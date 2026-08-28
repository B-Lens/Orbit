# Testnet daily review — 2026-08-27 UTC

Source: [Orbit Testnet daily report #568](https://github.com/ipankaj18/Orbit/issues/568)

## Outcome

- Trade attempts: 210
- Accepted signals: 2
- Orders submitted: 2
- Orders filled: 2
- Order-stage rejections: 0
- Strategy/risk rejections: 208
- Errors: 0
- No-signal evaluations: 174
- Net P&L after fees and funding: -17.81371451 USDT

Both accepted PAXGUSDT entries completed the submission, protective-order, and
fill lifecycle. The report contains no demonstrated order-execution failure.

## Cooldown analysis

All 208 strategy/risk rejections were recorded as `cooldown`. The ledger pattern
shows BTCUSDT blocked until approximately 04:30 UTC, ETHUSDT blocked throughout
the window, and PAXGUSDT blocked after each accepted entry. The previous event
schema cannot distinguish an open position from a post-exit cooldown, so the
ETHUSDT result cannot prove whether exposure remained open or a flat symbol was
incorrectly suppressed.

The code review found three causes of this ambiguity:

1. Active positions and post-exit cooldowns used the same rejection reason.
2. Position discovery treated a nonzero `entryPrice` as active without also
   requiring a nonzero `positionAmt`.
3. The cooldown deadline was repeatedly rewritten while a position remained
   open, instead of beginning on the open-to-closed transition.

## Remediation in this pull request

- Require both nonzero entry price and nonzero position quantity before treating
  broker exposure as active.
- Record `active_position` separately from `post_exit_cooldown`.
- Start the configured cooldown when a trade leaves active state.
- Reject unavailable symbols before loading market history or running a strategy.
- Persist position side, position quantity, and cooldown expiry with the decision.
- Reconcile realized P&L, commission, funding, other income, and net P&L in the
  daily report.

Historical `cooldown` rows remain immutable and are not relabeled. Reports
created after deployment will contain the new diagnostic fields.

## Validation

- Focused lifecycle/reporting tests: 36 passed.
- Full test suite: 127 passed.
- `git diff --check`: passed.
- Repository-wide Black baseline still reports unrelated pre-existing files that
  require formatting; all files changed by this work were formatted.
