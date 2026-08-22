Review this pull request as a strict production-safety gate for a continuously
running automated cryptocurrency trading system.

Inspect the complete diff against the supplied base commit and enough surrounding
code to prove each finding. Focus only on actionable correctness and safety bugs:

- crashes, unhandled exceptions, deadlocks, and stopped worker loops
- incorrect order side, quantity, leverage, price, stop-loss, or take-profit
- duplicate, missing, or non-idempotent exchange operations
- unsafe paper/Testnet/live environment separation
- stale, inconsistent, or corrupted Redis/MongoDB/exchange state
- incorrect P&L, fee, funding, drawdown, or risk-limit calculations
- credential disclosure, command injection, or privilege escalation
- deployment changes that can silently stop or misconfigure production

Do not report style, naming, formatting, documentation preferences, speculative
concerns, or pre-existing problems outside this pull request. Do not modify files.
Use only changed-file line numbers. Report a finding only when the diff provides
concrete evidence and explain the production failure scenario.

Verdict rules:

- PASS: no actionable findings; `findings` must be empty.
- FAIL: one or more actionable findings; `findings` must be non-empty.
- P0: immediate loss, credential compromise, or broad production outage.
- P1: probable crash, incorrect trade, bypassed safety control, or data corruption.
- P2: concrete lower-impact correctness defect that should still block merging.

Return only the JSON object required by the provided output schema.
