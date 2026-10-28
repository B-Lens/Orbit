# Orbit documentation

Use this page to find the smallest relevant document before changing or operating
Orbit.

## Architecture

- [Codebase map](architecture/CODEBASE_MAP.md): package ownership, runtime flow,
  safety invariants, and test boundaries.
- [Core runtime flow](architecture/core_module_flowchart.md): signal, execution,
  state, intelligence, and reporting interactions.

## Operations

- [Safe adaptive trading](operations/SAFE_ADAPTIVE_TRADING.md): execution modes,
  risk policy, accounting, rollout, promotion, and rollback.
- [Codex automation](operations/CODEX_AUTOMATION.md): automated issue and pull
  request workflow.
- [Codex PR review](operations/CODEX_PR_REVIEW.md): automated review behavior and
  repository setup.

## Strategy research

The files in [`research/`](research/) record asset-specific logic, backtest
assumptions, results, and promotion evidence. Treat them as research records;
runtime strategy ownership remains in `src/orbit/strategies/` and
`config/strategies.yaml`.

For local setup and common commands, return to the [project README](../README.md).
