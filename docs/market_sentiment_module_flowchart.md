# Market Sentiment Module Flow Chart

```mermaid
flowchart TD
    A[Start] --> B[Instantiate MarketSentimentModule]
    B --> C[Load Configurations & Environment Variables]
    C --> D[Initialize Logger & Dependencies]
    D --> E[Fetch Reddit Data]
    E --> F[Preprocess Reddit Data]
    F --> G[Analyze Reddit Sentiment]
    G --> H[Fetch News Articles]
    H --> I[Preprocess News Data]
    I --> J[Analyze News Sentiment using LLM]
    J --> K[Aggregate Sentiment Results]
    K --> L[Retrieve Market Indicators]
    L --> M[Calculate Unified Sentiment Score]
    M --> N[Persist Analysis Results to MongoDB]
    N --> O[Generate Trading Signals]
    O --> P[Send Notifications via Discord]
    P --> Q[Handle Exceptions & Logging]
    Q --> R[End]
```
