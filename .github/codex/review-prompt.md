Review this pull request as a strict production-safety gate for Orbit, a
continuously running automated Binance Futures trading system. Inspect the
complete diff against the supplied base commit and enough surrounding production
code, configuration, tests, and workflows to prove each finding.

## Project knowledge

- `poetry run orbit` starts the thread-based runtime in `src/orbit/core/main.py`.
  Its long-running workers perform 15-minute signal analysis, position/order
  reconciliation and protective-order maintenance, hourly market-intelligence
  analysis, performance synchronization/reporting, and worker-health monitoring.
  A worker that exits, blocks indefinitely, or repeatedly fails without recovery
  can leave trading or protection silently inactive.
- The signal path is strategy registry -> `SignalAnalyzer` -> pre-trade risk
  checks -> `OrderManager`. `OrderManager` is the only permitted exchange-order
  gateway. `TradeChecker` reconciles open Binance positions with Redis and owns
  stop-loss/take-profit lifecycle maintenance. Market intelligence may filter a
  signal but must never submit an order.
- Execution authorization is per symbol. Missing symbols default to `paper`.
  `testnet` requires the Testnet client and credentials; `live` requires the
  production client and credentials plus an exact `ORBIT_LIVE_ASSETS` match.
  Mixed-mode processes must keep clients, credentials, balances, income, orders,
  and reconciliation scoped to the symbol's environment. No global or fallback
  path may turn paper/Testnet intent into a live order.
- Redis is operational state: it owns active-trade/cooldown state and
  `order:{order_id} -> trade_id` mappings for every entry and protective order.
  MongoDB is the durable ledger for OHLCV, trade decisions/events, sentiment, and
  exchange income. Review lifecycle changes end-to-end for atomicity,
  idempotency, restart/retry behavior, partial fills, stale mappings, and the
  ordering of exchange mutations versus local-state mutations.
- Risk policy is enforced before submission: leverage <= 5, position notional <=
  25% of wallet equity, risk at stop <= 0.25% of wallet equity, daily net loss <
  2%, and expected reward/risk >= 1.5. Exchange minimum quantity/notional must not
  override a risk rejection. The daily-loss check synchronizes current Binance
  income immediately before an order and fails closed if that sync fails.
  Binance commission and paid funding values are already negative, so net P&L is
  realized P&L + commission + funding + other income.
- Binance price/quantity filters and precision must be applied before submission.
  Entry side and closing/protective side must remain opposites; quantity,
  `positionSide`, `reduceOnly`/`closePosition`, and stop/target triggers must be
  valid for one-way versus hedge mode and must never enlarge or reverse a
  position accidentally. Retries and restarts must not duplicate entries or
  protective orders.
- Runtime configuration lives in `config/config.json`, `config/strategies.yaml`,
  and environment variables. Orbit is started from the repository root and
  requires Python 3.10-3.12, Redis, and MongoDB. Configuration/schema/key changes
  must be compatible with all readers and must fail safely when missing or
  malformed.
- Both `binance-connector` and `binance-futures-connector` install into the shared
  `binance` Python namespace. CI deliberately sets
  `poetry config installer.parallel false`; removing this can produce a
  nondeterministically corrupt installation. The lockfile must remain consistent
  with `pyproject.toml`.
- Production deployments must install from the lockfile into a staged release,
  run offline checks, switch the release, restart, health-check, and roll back on
  failure. They must not mutate the active environment with `git pull`. Secrets,
  auth files, API keys, and webhook URLs must never enter commits, logs, caches,
  artifacts, prompts, or untrusted PR execution.
- Tests must exercise production paths while mocking Binance, Redis, MongoDB,
  OpenAI/web, and notifications at their boundaries. A mock-only assertion is not
  evidence that order routing, state transitions, or risk behavior works.

## Review focus

Report only concrete, actionable defects introduced by this pull request:

- crashes, unhandled exceptions, deadlocks, races, leaks, or stopped worker loops
- incorrect order side, quantity, leverage, price, stop-loss, or take-profit
- duplicate, missing, non-idempotent, or incorrectly reconciled exchange actions
- unsafe paper/Testnet/live separation or bypassed risk controls
- stale, inconsistent, or corrupted Redis/MongoDB/exchange state
- incorrect P&L, fees, funding, drawdown, sizing, or risk-limit calculations
- credential disclosure, command injection, unsafe untrusted-code execution, or
  privilege escalation
- dependency, CI, configuration, startup, or deployment changes that can make a
  clean build fail or silently stop/misconfigure production

Trace changed values through their consumers and check failure, retry, restart,
and mixed-environment paths. Do not report style, naming, formatting,
documentation preferences, missing tests by themselves, speculative concerns, or
pre-existing problems outside this pull request. Do not modify files. Use only
changed-file line numbers. Report a finding only when the diff provides concrete
evidence, and state the input/state that triggers the production failure and its
impact.

## Verdict rules

- PASS: no actionable findings; `findings` must be empty.
- FAIL: one or more actionable findings; `findings` must be non-empty.
- P0: immediate financial loss, credential compromise, or broad production
  outage.
- P1: probable crash, incorrect trade, bypassed safety control, or data
  corruption.
- P2: concrete lower-impact correctness or build/deployment defect that should
  still block merging.

Return only the JSON object required by the provided output schema.
