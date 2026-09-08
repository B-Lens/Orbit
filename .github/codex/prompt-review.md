Review this pull request for Orbit, a continuously running Binance Futures
trading system. Return only the JSON required by the review schema.

Review the full diff and relevant callers, configuration, tests, and workflows.
Report every independently actionable defect introduced by this diff, with a
changed-file line number, trigger, and production impact. Trace changed values
to their consumers. Ignore style, docs, missing tests alone, speculation,
pre-existing issues, and outage or system-failure scenarios (including external
service, exchange, network, worker, or infrastructure failures).

Project safety context:

- Only `OrderManager` may place exchange orders. `TradeChecker` reconciles
  positions/protective orders; market intelligence may filter signals only.
- Core-owned pre-trade LLM review is required and fails closed. Decision and
  exit reasoning must remain tied to one immutable lifecycle; post-exit review
  is observational and cannot delay or place orders.
- `config/strategies.yaml` authorizes each symbol as `testnet` or `live`.
  Missing/paper modes fail startup. All orders, balances, income, and
  reconciliation use that symbol's Binance environment; monitored positions
  also require an explicit mode.
- State and exchange mutations must be atomic/idempotent across partial fills,
  retries, restarts, stale mappings, and concurrent workers. Close side must
  oppose entry; protective orders must not enlarge or reverse a position.
- Limits: leverage <= 5; notional <= 25% equity; stop risk <= 0.25% equity;
  daily net loss < 2%; reward/risk >= 1.5. Exchange minimums cannot override a
  rejection. Income sync fails closed; commission and paid funding are already
  negative in net P&L.
- Do not allow safeguards to be silently disabled or credentials to enter code,
  logs, artifacts, prompts, or untrusted execution.

Verdict: PASS only when `findings` is empty; otherwise FAIL. P0 is immediate
financial loss or credential compromise; P1 is an incorrect trade, bypassed
safety control, or corruption; P2 is a merge-blocking correctness/build/deploy
defect.
