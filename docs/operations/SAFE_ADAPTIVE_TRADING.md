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

Execution is configured per asset in `config/strategies.yaml` with the singular
`execution_mode` field:

- Every configured strategy currently declares `execution_mode: testnet`.
- The system has exactly two execution modes: `testnet` and `live`; missing or
  invalid values fail startup validation.
- Symbols monitored only for existing positions are listed under
  `monitored_assets` with an explicit testnet or live environment.
- BTC, ETH, BCH, and PAXG orders all route to Binance Futures Testnet.

There is no environment-variable execution-mode override. Testnet uses
`BINANCE_TESTNET_API_KEY`, `BINANCE_TESTNET_SECRET_KEY`, and
`https://demo-fapi.binance.com`. Live assets require production credentials.

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

Sentiment-conflict rejections are stored only in this ledger. Orbit does not
create separate `contradict_trades` or `simulated_trades` collections: those
copies duplicated decision data and did not represent executed positions.

## Daily and weekly reports

The `/daily` and `/weekly` dashboard reports read Testnet decisions, execution
events, closed-trade lifecycles, and active lifecycle evidence from MongoDB.
Closed-trade counts and per-asset P&L use the durable lifecycle collection so
broker-reconstructed trades are included even when they have no originating
decision event. For periods ending within
the last 30 days, the API reads Testnet income and the USDT wallet entry from
the current Binance account response when Testnet credentials are configured.
It reconstructs the period-end USDT balance by subtracting subsequent USDT
income, then derives opening balance and percentages. It rejects missing USDT
wallet entries, non-USDT income, and income that changes during the account
snapshot. `PerformanceReporterThread` archives verified daily and completed
weekly periods in MongoDB `report_accounting`, with the closing USDT wallet
balance and exact exchange income rows used to calculate it. It refreshes the
last 30 completed IST days and weeks within that range when it starts and once
per day. Report requests remain read-only. If direct exchange verification is
unavailable, the API uses a verified archive when present. Otherwise it uses
the stored Testnet income ledger, labels its completeness unverified, and
leaves historical equity and percentages unavailable. Live and Testnet
records remain separate.

## Performance accounting

MongoDB collection `futures_income` upserts exchange income rows by transaction
and income type. The daily report calculates:

```text
trading net P&L = realized P&L + commission + funding fees
wallet change = trading net P&L + transfers + other income
trading return % = trading net P&L / opening USDT wallet * 100
wallet change % = wallet change / opening USDT wallet * 100
```

Daily reports distinguish closed-lifecycle net P&L from period account income.
The unclosed lifecycle table is historical ledger evidence, not an exchange-position
snapshot; use the command center's exchange-backed Active positions table for
current position state.

GitHub reports, `/daily`, `/weekly`, and the Closed trades calendar use IST
(Asia/Kolkata, UTC+05:30), regardless of the browser's timezone. Daily periods
run midnight to midnight; weeks run Saturday 00:00 to the following Saturday
00:00 (end exclusive). Weekly reports display both dates and the exact interval.
The publisher switches days at IST midnight. Existing published UTC reports
retain their old evidence until explicitly regenerated.

Daily and weekly pages show account trading net P&L, the closing USDT wallet,
trading return, wallet change, and realized trading drawdown. Details include
opening USDT wallet, realized P&L, commissions, funding, transfers, other
income, wallet change %, and drawdown %. The wallet balance and settled trading
drawdown exclude unrealized position P&L. Drawdown groups trading income with
identical exchange timestamps; percentage drawdown uses the corresponding
wallet peak with external cash flows excluded from its path. Transfers affect
wallet change but not trading net P&L or trading drawdown. These are account
figures, so activity outside Orbit in the same Testnet account can contribute.

The API service needs `BINANCE_TESTNET_API_KEY` and
`BINANCE_TESTNET_SECRET_KEY` to verify recent historical balances. Binance
income history is limited to the last three months; Orbit limits its direct
reconstruction to 30 days to keep report requests bounded. Archived verified
periods remain available after this window; periods that were never archived
cannot gain a historical wallet balance from trade records alone. Closed-trade
Net P&L is the sum of the displayed lifecycle records; the panel identifies
the 250-row display cap.

Binance represents commissions and paid funding as negative income, so they are
added rather than subtracted a second time. `PerformanceReporterThread` syncs and
reports the last 24 hours when Orbit starts and every 24 hours thereafter.

Reporting uses `trade_decisions`, `futures_income`, and `report_accounting`.
The verified accounting documents retain period income and closing wallet
balance together, so later income-ledger gaps cannot silently change a saved
report. The market-intelligence workflow also retains `sentiment_history`
because its rolling 24-hour score is an input to the current signal filter.

On Saturday IST, the Testnet reporter also publishes an idempotent report for the
completed Saturday-through-Friday week. It distinguishes accepted signals,
submitted orders, filled orders, order-stage rejections, and realized-PnL
events. The weekly scorecard includes fee-aware net P&L, realized-PnL profit
factor, ledger drawdown, protective-order failures, and symbol/strategy
breakdowns. It does not label realized-PnL rows as closed trades or estimate
slippage and uptime from data that the runtime does not persist.

Weekly scorecards do not receive `ai-autonomous` and therefore cannot start the
issue-implementation workflow.

## Risk policy

`config/config.json` contains policy independent of strategy logic:

- maximum leverage: 5
- maximum position notional: 25% of wallet equity
- maximum risk at stop: 1% of wallet equity per trade
- daily net-loss halt: 2% of wallet equity
- minimum expected reward/risk: 1.5

Position size is the smallest quantity allowed by stop-loss risk, maximum
position notional, and leveraged available margin. A 2% sizing buffer reserves
room for price movement, fees, and exchange rounding. The quantity is rounded
down to the exchange step size and validated again immediately before submission.
Exchange minimums do not override policy: if a minimum-sized order exceeds a
limit, it is rejected.

For example, with 1,000 USDT wallet equity, 200 USDT of unreserved available
margin, 2x leverage, a 100 USDT entry, a 99 USDT stop, 1% risk, and the default
25% notional limit, the independent limits are 10 units from stop risk, 2.5 units
from position notional, and 4 units from available margin. Orbit selects 2.5,
applies the 2% buffer, and submits at most 2.45 units after rounding down. The
position notional is 245 USDT and its required margin at 2x is 122.50 USDT. If
only 100 USDT were available, the margin limit would be 2 units and the buffered
quantity would instead be 1.96. Leverage changes margin usage; it does not expand
the configured position-notional or stop-risk limits.
Immediately before an order, the daily loss gate refreshes today's income from
Binance and persists it locally. If this authenticated synchronization fails, the
order path fails closed instead of trading with a stale daily-loss value.

## EC2 rollout checklist

1. Back up `.env`, MongoDB, and the current deployed commit IDs.
2. Install Orbit from its lockfile; no second repository is required.
3. Select `testnet` or `live` for every strategy and monitored asset in
   `config/strategies.yaml`. For the initial rollout, keep every mapping on
   `testnet` and load Testnet credentials.
4. Start Redis and MongoDB, then start Orbit from the repository root.
5. Confirm logs show Testnet mode and the expected Orbit commit.
6. Confirm `trade_decisions` receives no-signal and rejected decisions.
7. Place only deliberately triggered Testnet scenarios and reconcile orders in
   the Binance Testnet UI.
8. Confirm `futures_income` includes realized P&L, commissions, and funding.
9. Observe at least the agreed number of independent trades and market regimes.
10. Promote an asset only through a reviewed change from `execution_mode:
    testnet` to `execution_mode: live`, with production credentials provisioned
    outside the repository.
11. Review drawdown and net performance before approving another asset.

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
