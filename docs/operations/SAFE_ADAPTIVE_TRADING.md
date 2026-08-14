# Safe adaptive trading and performance operations

## Purpose

Orbit's adaptation loop improves risk-adjusted execution without allowing a
strategy or an AI process to rewrite live trading behavior autonomously.

```text
OHLCV + sentiment -> versioned strategy -> decision ledger -> risk guard
-> Testnet order -> Binance income ledger -> performance report -> promotion review
```

## Repositories

- `Orbit` owns data collection, sentiment filters, risk policy, order execution,
  trade monitoring, accounting, and notifications.
- `Orbit-Strategies` owns signal generation. Version 1.0.4 is currently expected.

Deploy both repositories at explicit commits. Do not use an unpinned editable
checkout on EC2. The running commit and strategy package version must be included
in deployment logs.

Install the audited strategy commit inside Orbit's Poetry environment with:

```bash
poetry run pip install -r strategy-requirements.txt
```

When promoting a strategy release, update the commit in that file and the
`expected_package_version` in `config/strategies.yaml` together.

## Execution environments

`ORBIT_EXECUTION_MODE` controls the order boundary:

- `paper` is the default and blocks all Binance order submissions.
- `testnet` uses `BINANCE_TESTNET_API_KEY`, `BINANCE_TESTNET_SECRET_KEY`, and
  `https://demo-fapi.binance.com` by default.
- `live` uses production credentials and additionally requires
  `ORBIT_LIVE_TRADING_ACK=I_UNDERSTAND`.

Testnet and production credentials must be different and should be loaded from
AWS Systems Manager Parameter Store or Secrets Manager by the EC2 service. Never
write credentials or webhook URLs into Git.

## Decision ledger

MongoDB collection `trade_decisions` stores accepted, rejected, no-signal, and
error decisions. Each entry includes a UUID, symbol, fully qualified strategy
class, strategy package version, execution mode, sentiment, pattern, prices,
outcome, and reason. This makes rejected opportunities measurable and connects
returns to the exact signal implementation.

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
2. Install the pinned `Orbit-Strategies` release and Orbit lockfile.
3. Set `ORBIT_EXECUTION_MODE=testnet` and Testnet-only credentials.
4. Start Redis and MongoDB, then start Orbit from the repository root.
5. Confirm logs show the Testnet mode and expected strategy version.
6. Confirm `trade_decisions` receives no-signal and rejected decisions.
7. Place only deliberately triggered Testnet scenarios and reconcile orders in
   the Binance Testnet UI.
8. Confirm `futures_income` includes realized P&L, commissions, and funding.
9. Observe at least the agreed number of independent trades and market regimes.
10. Review drawdown and net performance before any live-mode discussion.

## Deployment health and rollback

The EC2 webhook should perform a staged deployment: fetch explicit commits,
install into a new virtual environment, run offline tests, stop Orbit, switch the
release symlink, restart, and verify process health. If startup or health checks
fail, restore the prior symlink and restart the previous release. A webhook must
not run `git pull` directly inside the active environment.

Minimum deployment output should include deployment ID, both commit hashes,
execution mode, test result, service restart result, and rollback result. Keep
these events in journald/CloudWatch and send failures to `ORBIT_WEBHOOK_ALERTS`.

## Promotion criteria

Define these before collecting results: minimum trade count, maximum drawdown,
minimum profit factor, maximum daily loss, slippage allowance, and comparison
benchmark. Promotion remains a human-approved configuration/version change.
Automated rollback is allowed when a hard risk or health threshold is breached.

Use `orbit_strategies.backtesting.WalkForwardBacktester` for the first validation
stage. It exercises the production signal method using historical prefixes and
includes fees, slippage, conservative same-candle exits, equity sizing, profit
factor, and maximum drawdown. Strategy-specific research notebooks remain
exploratory evidence and are not deployment gates by themselves.
