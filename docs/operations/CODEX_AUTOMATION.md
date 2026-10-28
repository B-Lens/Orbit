# Codex issue and review automation

`.github/workflows/codex-automation.yml` delegates implementation and repair jobs
to the reusable workflow in `b-lens/code-kit`. Orbit owns the triggers,
permissions, reviewer, repository-specific instructions, and authentication
binding; `code-kit` owns the runner implementation.

## Triggers

- Adding `ai-autonomous` to an issue starts an implementation run from `main`.
  The run creates a focused branch and, when changes are warranted, opens a pull
  request that follows Orbit's template and requests review from `ipankaj18`.
- A `changes_requested` review starts a repair run only when the pull request
  branch belongs to this repository. The run verifies the feedback, updates the
  existing branch when needed, and requests re-review.
- Both jobs use the `Codex-Automation` environment, which should require approval
  by a trusted reviewer before the `CODEX_AUTH_JSON` secret becomes available.

## Safety boundaries

- Issue, review, and repository text are untrusted input and must not override
  the task or expose credentials.
- Implementation is limited to the prepared issue branch and one pull request;
  repair is limited to the reviewed pull request branch.
- Automation cannot merge or approve its own changes. Generated commits still
  require CI, the production-safety review, and human approval.
- Trading changes must preserve execution modes, risk limits, sentiment gates,
  and order-lifecycle safeguards.
- Codex credentials are written to temporary runner storage and removed during
  cleanup. Rotate them after any suspected exposure.

A policy rejection or infrastructure failure is not by itself a software defect,
and automation must not weaken safeguards to make an issue pass.

The reusable workflow reference currently tracks `code-kit`'s `main` branch, so
its pinned tool versions and runner mechanics may change without an Orbit commit.
Inspect the referenced workflow for current implementation details.
