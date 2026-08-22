# Safe adaptive trading and performance operations

## Purpose

Orbit's adaptation loop improves risk-adjusted execution without allowing a
strategy or an AI process to rewrite live trading behavior autonomously.

```text
OHLCV + sentiment -> versioned strategy -> decision ledger -> risk guard
-> Testnet order -> Binance income ledger -> performance report -> promotion review
```

## Repository ownership

Orbit owns the complete production runtime: data collection, the active
strategies, sentiment filters, risk policy, execution, monitoring, accounting,
notifications, and backtesting. `Orbit-Strategies` is a legacy research archive
and is not installed by CI or EC2.

Production strategies live in `src/orbit/strategies/`. Every strategy change is
therefore versioned by the same Orbit commit that executes it. Experimental
research should not be imported into the runtime until it is reduced to the
production strategy contract and reviewed in Orbit.

## Execution environments

Execution is configured only per asset with `ORBIT_ASSET_EXECUTION_MODES`:

- `BCHUSDT:testnet` routes only BCH orders to Binance Futures Testnet.
- Symbols omitted from the mapping remain paper-only.
- `BCHUSDT:live` routes only BCH orders to production and is accepted only when
  `ORBIT_LIVE_ASSETS=BCHUSDT` exactly matches every live mapping.

There is no global execution-mode setting, so approving one asset cannot unlock
the rest of the automation. Testnet uses `BINANCE_TESTNET_API_KEY`,
`BINANCE_TESTNET_SECRET_KEY`, and `https://demo-fapi.binance.com`. Live assets
use the production credentials. A mixed mapping creates and routes through
separate clients; credentials are never shared between environments.

Testnet and production credentials must be different and should be loaded from
AWS Systems Manager Parameter Store or Secrets Manager by the EC2 service. Never
write credentials or webhook URLs into Git.

## Decision ledger

MongoDB collection `trade_decisions` stores accepted, rejected, no-signal, and
error decisions. Each entry includes a UUID, symbol, fully qualified strategy
class, strategy package version, execution mode, sentiment, pattern, prices,
outcome, and reason. This makes rejected opportunities measurable and connects
returns to the exact signal implementation.

Order-stage failures append an immutable execution event with a machine-readable
reason such as `minimum_notional`, `daily_loss_limit`, `paper_mode`, or
`exchange_client_error`. This preserves the difference between a strategy being
accepted and its exchange order being rejected.

## GitHub Testnet reporting and analysis

When `ORBIT_GITHUB_REPORTING_ENABLED=true`, `TestnetDailyReporterThread` publishes
the previous UTC day's accepted, rejected, and errored Testnet attempts to one
idempotent GitHub issue and adds it to the configured Project. The issue includes
prices, sentiment, strategy identity, decision reason, all execution transitions,
and fee-aware net P&L. No-signal evaluations are counted but are not trade attempts.
Before publication, the reporter synchronizes Binance Testnet income from the
start of the reporting window. Income rows are tagged by execution mode, and the
report queries only `testnet` rows so mixed live/Testnet deployments cannot blend
account performance.

The publisher then applies `ai-autonomous`. The existing Codex workflow analyzes
the evidence and may create a reviewed pull request only for a demonstrated code
defect. Its task explicitly forbids weakening risk limits, bypassing sentiment, or
enabling live trading. Keep the `Codex-Automation` environment approval required.

Configure the EC2 service with a fine-grained GitHub token limited to Issues
(write) on `ipankaj18/Orbit` and Projects (write) on the private Project:

```text
ORBIT_GITHUB_REPORTING_ENABLED=true
ORBIT_GITHUB_TOKEN=<secret supplied by the service manager>
ORBIT_GITHUB_REPOSITORY=ipankaj18/Orbit
ORBIT_GITHUB_PROJECT_ID=PVT_kwHOBPU1Qs4BhHzU
```

Never store the token in `.env` inside the checkout, logs, MongoDB, or GitHub.

## Performance accounting

MongoDB collection `futures_income` upserts exchange income rows by transaction
and income type. The daily report calculates:

```text
net P&L = realized P&L + commission + funding fees + other income
return % = net P&L / opening equity * 100
```

Binance represents commissions and paid funding as negative income, so they are
added rather than subtracted a second time. `PerformanceReporterThread` syncs and
reports the last 24 hours when Orbit starts and every 24 hours thereafter.

## Risk policy

`config/config.json` contains policy independent of strategy logic:

- maximum leverage: 5
- maximum position notional: 25% of wallet equity
- maximum risk at stop: 1% of wallet equity per trade
- daily net-loss halt: 2% of wallet equity
- minimum expected reward/risk: 1.5

Position size is derived from wallet equity and stop distance. Exchange minimums
do not override policy: if a minimum-sized order exceeds a limit, it is rejected.
Immediately before an order, the daily loss gate refreshes today's income from
Binance and persists it locally. If this authenticated synchronization fails, the
order path fails closed instead of trading with a stale daily-loss value.

## EC2 rollout checklist

1. Back up `.env`, MongoDB, and the current deployed commit IDs.
2. Install Orbit from its lockfile; no second repository is required.
3. For the current rollout, set
   `ORBIT_ASSET_EXECUTION_MODES=BTCUSDT:testnet,ETHUSDT:testnet,BCHUSDT:testnet`
   and load Testnet-only credentials. Omitted assets remain paper-only.
4. Start Redis and MongoDB, then start Orbit from the repository root.
5. Confirm logs show Testnet mode and the expected Orbit commit.
6. Confirm `trade_decisions` receives no-signal and rejected decisions.
7. Place only deliberately triggered Testnet scenarios and reconcile orders in
   the Binance Testnet UI.
8. Confirm `futures_income` includes realized P&L, commissions, and funding.
9. Observe at least the agreed number of independent trades and market regimes.
10. To approve that asset for live, change only its mapping to `live`, add the
    same symbol to `ORBIT_LIVE_ASSETS`, deploy, and verify the decision ledger.
11. Review drawdown and net performance before approving another asset.

## Testnet order integration CI

`.github/workflows/testnet-order-integration.yml` exercises the production
`OrderManager` path for every symbol in `config/config.json` `trading_pairs`.
It fetches current Testnet filters and balance, applies the configured precision,
position sizing, leverage, stop/target geometry, and risk policy, submits a deeply
off-market limit order with `ros=True`, and cancels the order in `finally`.

Create a protected GitHub environment named `Testnet-Integration`, store
`BINANCE_TESTNET_API_KEY` and `BINANCE_TESTNET_SECRET_KEY` there, and set the
repository variable `ORBIT_RUN_TESTNET_ORDER_TESTS=true`. The workflow runs on
manual dispatch, daily, and after relevant changes reach `main`; it never runs on
pull requests and hard-checks the `https://demo-fapi.binance.com` endpoint. Keep
only disposable Testnet funds in this account. A newly added trading asset must
also define precision, risk allocation, fixed allocation, and a Testnet-compatible
strategy or the credential-free configuration test fails in normal unit CI.

## Deployment health and rollback

The EC2 webhook should perform a staged deployment: fetch explicit commits,
install into a new virtual environment, run offline tests, stop Orbit, switch the
release symlink, restart, and verify process health. If startup or health checks
fail, restore the prior symlink and restart the previous release. A webhook must
not run `git pull` directly inside the active environment.

Minimum deployment output should include deployment ID, the Orbit commit hash,
execution mode, test result, service restart result, and rollback result. Keep
these events in journald/CloudWatch and send failures to `ORBIT_WEBHOOK_ALERTS`.

## Promotion criteria

Define these before collecting results: minimum trade count, maximum drawdown,
minimum profit factor, maximum daily loss, slippage allowance, and comparison
benchmark. Promotion remains a human-approved configuration/version change.
Automated rollback is allowed when a hard risk or health threshold is breached.

Use `orbit.backtesting.WalkForwardBacktester` for the first validation
stage. It exercises the production signal method using historical prefixes and
includes fees, slippage, conservative same-candle exits, equity sizing, profit
factor, and maximum drawdown. Strategy-specific research notebooks remain
exploratory evidence and are not deployment gates by themselves.
