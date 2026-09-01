# Repository Guidelines

## Project Structure & Module Organization

Application code uses a `src` layout under `src/orbit/`. Runtime orchestration and exchange safeguards live in `core/`; trading algorithms belong in `strategies/`; historical simulation is in `backtesting/`; and news, social, and LLM integrations are grouped in `market_intelligence/`. Keep shared helpers in `src/orbit/utils/`. Repository-level configuration is stored in `config/`, operational and architecture notes in `docs/`, executable utilities in `scripts/`, and generated backtest artifacts in `results/`. Tests mirror behavior in the top-level `tests/` directory.

## Build, Test, and Development Commands

- `poetry install` installs runtime and development dependencies for Python 3.10–3.12.
- `cp .env.example .env` creates local configuration; never commit populated credentials.
- `poetry run orbit` starts the signal loop and background services. Redis and MongoDB must be available.
- `poetry run pytest` runs the complete test suite.
- `poetry run pytest tests/test_eth_strategy.py -q` runs one focused test module.
- `poetry run mypy src/orbit/market_intelligence/*.py` performs the documented strict type check.
- `poetry run pylint --disable=W,R,C,I --ignore=.venv .` runs the project lint command.
- `poetry run black --check <changed-python-files>` checks formatting without modifying files. Do not run repository-wide formatting as part of an unrelated change.

## Coding Style & Naming Conventions

Use four-space indentation and Black-compatible Python formatting. Format newly created Python files when needed, but only check existing touched files; do not rewrite unrelated lines unless formatting is explicitly part of the task. Add type annotations to new or changed interfaces; `mypy.ini` enables strict checking. Name modules, functions, and variables with `snake_case`, classes with `PascalCase`, and constants with `UPPER_SNAKE_CASE`. Keep strategy implementations in `<asset>_strategy.py` and register them through `strategy_registry.py`. Route exchange orders through `OrderManager`; market-intelligence code must not place orders directly.

## Testing Guidelines

Tests use pytest and `unittest` classes/mocks. Name files `test_<subject>.py` and test methods `test_<behavior>`. Exercise production behavior while mocking Binance, Redis, MongoDB, Discord, and LLM boundaries. Add regression tests for bug fixes and cover paper, testnet, and live safety gates when changing execution logic. No numeric coverage threshold is configured; prioritize risk and order-lifecycle paths.

## Commit & Pull Request Guidelines

Recent history generally uses short, imperative Conventional Commit prefixes such as `feat:`, `fix:`, `refactor:`, `docs:`, and `chore:`. Keep each commit focused. Pull requests should explain motivation and operational risk, link relevant issues, list validation commands, and update configuration or architecture documentation when behavior changes. Include screenshots only for visual documentation changes and never include secrets, API keys, webhook URLs, or external authentication files.

## Pull Request Requirements

Before creating or updating any pull request:

1. Read `.github/pull_request_template.md` completely.
2. Use every applicable section from the template in the pull request description.
3. Fetch `origin` and rebase the pull request branch onto `origin/main` before creating the pull request to avoid merge conflicts.
4. Do not call `gh pr create` until the description has been checked against the template.
5. Add `ipankaj18` as a reviewer when creating the pull request.
6. After creation, inspect the published pull request body and reviewer assignment, and correct any missing sections or reviewer.
