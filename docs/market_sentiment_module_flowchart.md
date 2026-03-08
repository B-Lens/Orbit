# Market Sentiment Module Flow Chart

```mermaid
flowchart TD
    subgraph Entry
      A[Start]
      B[Main Entry: Execute market_sentiment_module]
      C[Load Configurations & Environment Variables]
      D[Initialize Logger & Dependencies]
    end
    subgraph Data_Flow
      E[Fetch Reddit Data]
      F[Preprocess Reddit Data]
      G[Analyze Reddit Sentiment]
      H[Fetch News Articles]
      I[Preprocess News Data]
      J[Analyze News Sentiment using LLM]
    end
    subgraph Processing
      K[Aggregate Sentiment Data]
      L[Retrieve Market Indicators]
      M[Calculate Unified Sentiment Score]
    end
    subgraph Outputs
      N[Persist Analysis Results to MongoDB]
      O[Generate Trading Signals]
      P[Send Notifications via Discord]
      Q[Outputs: Metrics & Alerts]
      R[Handle Exceptions & Logging]
    end
    S[End]
    
    A --> B
    B --> C
    C --> D
    D --> E
    E --> F
    F --> G
    D --> H
    H --> I
    I --> J
    G --> K
    J --> K
    K --> L
    L --> M
    M --> N
    N --> O
    O --> P
    P --> Q
    Q --> R
    R --> S
```
