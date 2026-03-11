# Core Module Flowchart

Below is a flowchart that illustrates the architecture and execution flow of the core module.

```mermaid
flowchart TD
    A[BinanceAutomation] --> B[AuthenticationManager]
    A --> C[ExceptionManager]
    A --> D[DiscordManager]
    A --> E[MongoHandler]
    A --> F[OrderManager]
    A --> G[TradeChecker]
    A --> H[SignalAnalyzer]
    A --> I[SentimenCron]

    subgraph Core Module Components
        B[AuthenticationManager]
        C[ExceptionManager]
        D[DiscordManager]
        E[MongoHandler]
        F[OrderManager]
        G[TradeChecker]
        H[SignalAnalyzer]
        I[SentimenCron]
    end

    %% Interaction flows
    B -- "Loads config" --> E
    C -- "Handles exceptions" --> D
    D -- "Sends notifications" --> C
    F -- "Places orders" --> B
    G -- "Monitors orders" --> F
    H -- "Analyzes signals" --> F
    I -- "Runs periodic tasks" --> H

    %% Coordination arrows from BinanceAutomation
    A --- B
    A --- C
    A --- D
    A --- E
    A --- F
    A --- G
    A --- H
    A --- I
```

This diagram shows the major components of the core module and their interactions.
