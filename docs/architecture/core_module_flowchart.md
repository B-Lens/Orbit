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
    Result --> Decision[(MongoDB trade decisions)]
    Result --> Orders[OrderManager]

    Orders --> Risk[Execution mode and risk guard]
    Risk -->|accepted| Binance[Binance Futures]
    Risk -->|rejected| Decision
    Orders <--> State[(Redis trade and order state)]
    Orders --> Income[(MongoDB exchange income)]

    Checker <--> Binance
    Checker <--> State
    Checker --> MarketData[(MongoDB OHLCV)]

    Cron --> Codex[Codex-authenticated Responses API with web search]
    Codex --> Validate[Validate label, confidence, explanation, and sources]
    Validate -->|valid| SentimentDB[(MongoDB sentiment history)]
    Validate -->|valid| State
    Validate -->|invalid or unavailable| Preserve[Preserve cached sentiment]

    Reporter <--> Binance
    Reporter --> Income

    Monitor --> Alerts[Discord alerts]
    Signal --> Alerts
    Checker --> Alerts
    Cron --> Alerts
```

Market intelligence only updates the sentiment filter. An exchange order still
requires a strategy signal, an enabled per-asset execution mode, and acceptance
by the risk guard.
