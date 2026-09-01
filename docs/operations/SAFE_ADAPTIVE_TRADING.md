# Safe adaptive trading operations

Orbit may analyze trading results, but strategies and AI processes must not
autonomously change live trading behavior. All strategy or execution changes
require backtesting, Testnet validation, and human review.

## Execution and credentials

Configure each strategy and monitored asset in `config/strategies.yaml` with an
explicit `execution_mode` of `testnet` or `live`. Missing or invalid modes fail
startup. There is no environment-variable override for execution mode.

Use separate Testnet and production credentials. Supply credentials through the
deployment service or a secret manager; never commit them or write them to logs,
MongoDB, or GitHub.

## Safety policy

`config/config.json` defines limits independent of strategy logic:

- maximum leverage: 5
- maximum position notional: 25% of wallet equity
- maximum risk at stop: 0.25% of wallet equity per trade
- daily net-loss halt: 2% of wallet equity
- minimum expected reward/risk: 1.5

Position size is capped by stop risk, position notional, and available leveraged
margin, then buffered and rounded down to the exchange step size. Exchange
minimums never override a safety limit. Before submission, Orbit refreshes the
daily income ledger; failure to refresh causes the order path to fail closed.

All exchange orders must pass through `OrderManager`. Market-intelligence and
post-trade analysis code must not place orders or change risk limits.

## Operational ledgers

MongoDB stores:

- `OHLCVData`: market candles.
- `sentiment_history`: rolling sentiment used by signal filters.
- `trade_decisions`: signals, rejections, strategy identity, prices, and
  immutable execution events.
- `futures_income`: exchange-realized P&L, commission, funding, and other income.
- `trade_reviews`: one idempotent post-trade review per `decision_id`.

The authoritative account calculation is:

```text
net P&L = realized P&L + commission + funding fees + other income
```

Binance records paid commission and funding as negative income, so these values
are added without reversing their sign.

## Post-trade reviews

After broker reconciliation confirms a flat position, Orbit writes a
`trade_reviews` record before removing active Redis state. It retains the signal
context, weighted closing-fill price, outcome classification, realized P&L, and
commissions from Binance account trades. Failed review writes are queued in
Redis and retried without delaying position cleanup or cooldown.

Set `ORBIT_POST_TRADE_LLM_ENABLED=true` to add an LLM explanation for losing
trades. Suggestions are stored only as `status=observation`. Execution does not
read them, and they cannot activate filters, alter sizing, weaken risk limits, or
enable live trading.

## Testnet reporting

Set `ORBIT_GITHUB_REPORTING_ENABLED=true` to publish idempotent daily Testnet
reports and weekly scorecards. Reports separate accepted signals, rejections,
fills, execution failures, and exchange income by execution mode.

Daily reports may start the approval-gated Codex workflow through the
`ai-autonomous` label. The workflow may propose a reviewed fix for demonstrated
defects, but cannot weaken safety controls or enable live trading. Weekly reports
do not trigger autonomous changes.

Required service configuration:

```text
ORBIT_GITHUB_REPORTING_ENABLED=true
ORBIT_GITHUB_TOKEN=<secret supplied by the service manager>
ORBIT_GITHUB_REPOSITORY=ipankaj18/Orbit
ORBIT_GITHUB_PROJECT_ID=PVT_kwHOBPU1Qs4BhHzU
```

## Validation and promotion

Before promoting an asset to live trading:

1. Keep the asset on Testnet and verify decision, order, review, and income
   records against Binance.
2. Define minimum trade count, maximum drawdown, minimum profit factor, maximum
   daily loss, slippage allowance, and a comparison benchmark.
3. Run `orbit.backtesting.WalkForwardBacktester` with fees, slippage, and
   conservative same-candle exits.
4. Validate across multiple market regimes.
5. Change `execution_mode` to `live` only in a reviewed change.

Automated rollback is allowed for hard risk or health breaches. Deployments must
use explicit commits, run offline tests, verify service health, and restore the
previous release if startup or health checks fail.
