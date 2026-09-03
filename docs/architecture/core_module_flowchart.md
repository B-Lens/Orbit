# Core runtime flow

This diagram is the authoritative high-level view of the running application.

```mermaid
flowchart TD
    Start[poetry run orbit] --> Main[BinanceAutomation]

    Main --> Signal[SignalAnalyzer worker]
    Main --> Checker[TradeChecker worker]
    Main --> Cron[Croner worker]
    Main --> Reporter[PerformanceReporter worker]
    Main --> Monitor[Worker health monitor]

    Signal --> Registry[Strategy registry]
    Registry --> Strategy[Symbol strategy]
    Strategy --> Result[Signal result]
    Result --> EntryReview[TradeReasoner pre-trade review]
    EntryReview -->|approved| Orders[OrderManager]
    EntryReview --> Decision[(MongoDB decision ledger)]

    Orders --> Risk[Execution mode and risk guard]
    Risk -->|accepted| Binance[Binance Futures]
    Risk -->|rejected| Decision
    Orders --> Decision
    Orders <--> State[(Redis trade and order state)]
    Orders --> Income[(MongoDB exchange income)]

    Checker <--> Binance
    Checker <--> State
    Checker --> MarketData[(MongoDB OHLCV)]
    Checker -->|broker confirms flat| ExitReview[TradeReasoner post-exit review]
    ExitReview --> Lifecycle[(MongoDB completed-trade lifecycle)]
    Lifecycle --> Aggregates[(MongoDB duration and P&L aggregates)]

    Cron --> Codex[Codex-authenticated Responses API with web search]
    Codex --> Validate[Validate label, confidence, explanation, and sources]
    Validate -->|valid| SentimentDB[(MongoDB sentiment history)]
    Validate -->|valid| State
    Validate -->|invalid or unavailable| Preserve[Preserve cached sentiment]

    Reporter <--> Binance
    Reporter --> Income

    Main --> Daily[TestnetDailyReporter worker]
    Daily --> Decision
    Daily --> Income
    Daily --> Project[GitHub issue and Project item]

    Monitor --> Alerts[Discord alerts]
    Signal --> Alerts
    Checker --> Alerts
    Cron --> Alerts
```

Market intelligence only updates the sentiment filter. An exchange order still
requires a strategy signal, a successful pre-trade LLM approval, an enabled
per-asset execution mode, and acceptance by the risk guard. The LLM never places
orders: entry reasoning gates the handoff to `OrderManager`, while exit reasoning
runs only after Binance confirms the position is flat. Entry, execution, and exit
events remain linked by the decision identifier so daily Testnet reports can
publish auditable lifecycle evidence to the configured GitHub Project.
