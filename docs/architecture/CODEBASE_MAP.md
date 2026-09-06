# Codebase map

Use this page as the entry point when loading Orbit into a limited context window.

| Area | Responsibility | Start here |
| --- | --- | --- |
| Runtime | Starts and monitors long-running workers | `core/main.py` |
| Signals | Loads strategies and turns signals into orders | `core/signal_analyzer.py`, `strategies/strategy_registry.py` |
| Execution | Enforces environment modes, risk, and order lifecycle | `core/execution.py`, `core/risk_manager.py`, `core/order_manager.py` |
| Positions | Maintains stops, targets, cooldowns, and live prices | `core/trade_checker.py`, `core/binance_ws_manager.py` |
| State | Owns MongoDB market data and Redis trade mappings | `core/mongo_handler.py`, `core/redis_manager.py` |
| Intelligence | Runs Codex-authenticated web analysis and persists validated sentiment | `core/sentimen_cron.py`, `market_intelligence/sentimental_workflow.py` |
| LLM providers | Owns provider clients, routing, and reusable prompts independently of market intelligence | `llm/` |
| Trade reasoning | Gates every candidate entry and reviews every confirmed exit in the execution core | `core/trade_reasoner.py`, `core/main.py`, `core/trade_checker.py` |
| Testnet reporting | Publishes daily decision-ledger evidence to the linked GitHub Project for guarded Codex analysis | `core/testnet_reporter.py` |
| Strategies | Implements production and research signal logic | `strategies/` |

All package paths above are relative to `src/orbit/`. Configuration belongs in
`config/`; operational automation belongs in `.github/`; tests mirror behavior
boundaries in `tests/`.

## Runtime flow

`main` starts the signal analyzer, trade checker, sentiment cron, and performance
reporter. The signal
analyzer selects a strategy through the registry, then delegates risk and exchange
work to the order manager. Redis stores active trades and order-to-trade mappings;
MongoDB stores OHLCV, sentiment, decisions, income history, completed-trade
lifecycle records, and aggregate duration/P&L distributions. Before order
submission, the core asks the LLM to approve or reject each candidate using the
strategy signal and cached market intelligence. The trade checker reconciles Redis
with Binance, maintains protective orders, and asks the LLM for a post-exit review
after the broker confirms a position is flat. Live position prices come from each
symbol's periodic Binance Futures ticker stream; the checker falls back to REST
when those updates exceed its freshness limit. The sentiment cron
runs web-grounded Responses analysis every 30 minutes using provisioned Codex
credentials.

## Safety invariants

- The only execution modes are testnet and live; every asset must select one.
- Testnet and live modes require credentials for their selected exchange environment.
- `OrderManager` is the only exchange-order gateway.
- Missing, failed, or malformed pre-trade LLM reviews fail closed.
- Entry and exit LLM reasoning, execution rejections, and other trade blocks are
  appended to the MongoDB decision ledger. Active-position and post-exit cooldown
  states remain availability safeguards rather than LLM-reviewed candidates.
- `RedisManager` owns trade and order key formats.
- External services must be mocked at their boundary in unit tests.

## Tests

Run `poetry run pytest`. Tests must exercise production behavior: avoid assertions
that only verify a mock, a locally constructed dictionary, or an external service.
Parametrize or share setup when scenarios use the same production path.

See the [core runtime flow](core_module_flowchart.md) and
[safe operations](../operations/SAFE_ADAPTIVE_TRADING.md) for deeper detail.
