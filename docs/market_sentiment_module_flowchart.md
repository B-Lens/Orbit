# Market Sentiment Module Flow Chart

```mermaid
flowchart TD
    A[Start] --> B[Initialize MarketSentimentModule]
    B --> C[Load Configurations & Environment Variables]
    C --> D[Fetch Reddit Data]
    D --> E[Preprocess Reddit Data]
    E --> F[Analyze Reddit Sentiment]
    F --> G[Fetch News Articles]
    G --> H[Preprocess News Data]
    H --> I[Analyze News Sentiment (using LLM)]
    I --> J[Aggregate Sentiment Results]
    J --> K[Retrieve Market Indicators]
    K --> L[Calculate Unified Sentiment Score]
    L --> M[Persist Analysis to MongoDB]
    M --> N[Generate Trading Signals]
    N --> O[Send Notifications via Discord]
    O --> P[End]
```
