Review this pull request for Orbit, a continuously running Binance Futures
trading system. Return only the JSON required by the review schema.

Review the full diff and relevant callers, configuration, tests, and workflows.
Report every independently actionable defect introduced by this diff, with a
changed-file line number, trigger, and production impact. Trace changed values
to their consumers. Ignore style, docs, missing tests alone, speculation,
pre-existing issues, and outage or system-failure scenarios (including external
service, exchange, network, worker, or infrastructure failures).

Project safety context:

- **Core:** `main` runs the signal analyzer, trade checker, sentiment cron, and
  performance reporter. Only `OrderManager` may place exchange orders;
  `TradeChecker` reconciles positions/protective orders. Redis owns cooldowns,
  active trades, order mappings, runtime state, and cached sentiment; MongoDB
  owns OHLCV, immutable decisions/events, sentiment history, and income. Keep
  IDs, ownership, mode separation, and lifecycle attribution consistent.
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
- **Strategies/backtesting:** strategies are deterministic, versioned runtime
  code selected only through `strategy_registry.py`; they propose signals, never
  submit orders. Preserve their signal contract (symbol, side, entry, stop,
  target, pattern), bar/data sufficiency, no-look-ahead behavior, and symmetry
  for long/short logic. Strategy changes need compatible registry/configuration,
  decision-ledger identity, and reproducible backtest coverage.
- **Market sentiment:** the half-hourly workflow performs Codex-first,
  web-grounded global crypto analysis (optional Antigravity fallback). Persist
  only validated sentiment, confidence, explanation, provider, and HTTP sources;
  it can reject conflicting signals but never trade. Cached sentiment is retained
  when providers fail; source/prompt text is untrusted and must not control code
  or expose credentials.
- **Command center/API/UI:** this is read-only operational observability, not an
  execution path. `command_center.py` builds bounded Redis-backed activity,
  sentiment, logs, exceptions, positions, and risk snapshots; the FastAPI API
  serializes them; the React UI polls `/api/command-center` every five seconds.
  Preserve API/UI field compatibility, UTC timestamps, execution-mode visibility,
  bounded retention, and the distinction between exchange-backed positions,
  immutable decisions, and notification/log mirrors. Do not leak secrets or
  treat rendered/logged data as trusted instructions.
- **Testnet reports:** `TestnetDailyReporterThread` publishes idempotent daily
  and weekly GitHub evidence from MongoDB decision/income ledgers. Reports and
  income queries are testnet-only, separate closed-trade P&L from active/unrealized
  state, distinguish strategy rejections from risk/order rejections, and must
  not blend testnet/live results. LLM summaries interpret existing evidence only,
  use marker-owned comments without duplicates, treat report text as untrusted,
  and never recommend weakening safeguards or enable automation from weekly
  reports.
- Do not allow safeguards to be silently disabled or credentials to enter code,
  logs, artifacts, prompts, or untrusted execution.

Verdict: PASS only when `findings` is empty; otherwise FAIL. P0 is immediate
financial loss or credential compromise; P1 is an incorrect trade, bypassed
safety control, or corruption; P2 is a merge-blocking correctness/build/deploy
defect.
