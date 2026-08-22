# Orbit 🪐

Orbit is a Python framework for researching and running guarded Binance Futures
strategies. It combines deterministic strategy signals, per-asset execution modes,
pre-trade risk checks, position monitoring, and half-hourly web-grounded market
intelligence.

## Runtime

`poetry run orbit` runs the signal loop and starts background services:

| Worker | Responsibility |
| --- | --- |
| Signal analyzer | Runs configured strategies on 15-minute boundaries and submits accepted signals to `OrderManager`. |
| Trade checker | Reconciles positions and maintains stop-loss/take-profit orders. |
| Sentiment cron | Runs global, web-grounded crypto sentiment analysis every 30 minutes through OpenAI. |
| Performance reporter | Synchronizes exchange income and reports net performance when order submission is enabled. |
| Health monitor | Alerts when a background worker stops. |

Redis stores cooldowns, sentiment, trades, and order mappings. MongoDB stores
OHLCV history, sentiment results, trade decisions, and exchange income.

## Safety model

- There are exactly two execution modes: `testnet` and `live`. Every configured
  strategy and monitored position symbol selects one explicitly; all checked-in
  mappings currently use `testnet`.
- Startup rejects missing, paper, and invalid execution modes.
- `OrderManager` is the only exchange-order gateway.
- The daily-loss gate fails closed when exchange income cannot be synchronized.
- Market intelligence can filter signals but cannot place orders.

See [safe adaptive trading](docs/operations/SAFE_ADAPTIVE_TRADING.md) for the
risk policy, rollout, accounting, and rollback procedures.

## Setup

Requirements: Python 3.10–3.12, Poetry, Redis, and MongoDB.

```bash
git clone https://github.com/ipankaj/Orbit.git
cd Orbit
poetry install
cp .env.example .env
poetry run orbit
```

`config/strategies.yaml` is the single source of truth for strategy ownership and
order mode. BTC, ETH, BCH, and PAXG are currently pinned to Futures Testnet;
changing an asset to `live` routes that asset to Binance Futures production and
requires production credentials. Missing, paper, and invalid modes fail startup.
Symbols monitored for pre-existing positions are assigned an environment under
`monitored_assets` in the same file.

Market intelligence uses `OPENAI_AUTH_FILE`, pointing to a provisioned Codex
CLI `auth.json`. Credentials must stay outside the repository. Each run fails
closed and preserves the cached sentiment if web-grounded analysis fails.

## Configuration

| File | Purpose |
| --- | --- |
| `config/config.json` | Symbols, leverage, fixed allocations, precision, cooldowns, and risk limits. |
| `config/strategies.yaml` | Symbol-to-strategy ownership and allowed execution modes. |
| `config/webhooks.yaml` | Discord channel mapping; URLs come from environment variables. |
| `.env` | Testnet/live credentials and service endpoints; it does not control execution modes. |

## Architecture

Start with the [codebase map](docs/architecture/CODEBASE_MAP.md), then use the
[core runtime flowchart](docs/architecture/core_module_flowchart.md) for component
interactions. Operational automation is documented in
[Codex automation](docs/operations/CODEX_AUTOMATION.md) and
[Codex PR review](docs/operations/CODEX_PR_REVIEW.md).

Testnet candidates retain concise promotion evidence and future success criteria
in the [ETH dossier](docs/research/ETH_STRATEGY.md) and
[PAXG dossier](docs/research/PAXGUSDT_STRATEGY.md).

## Development

```bash
poetry run pytest
poetry run mypy src/orbit/market_intelligence/*.py
poetry run pylint --disable=W,R,C,I --ignore=.venv .
```

Tests should exercise production behavior with external services mocked at their
boundaries. Submit changes through a focused pull request after the full suite
passes.

## License

Orbit is licensed under the [MIT License](LICENSE).
