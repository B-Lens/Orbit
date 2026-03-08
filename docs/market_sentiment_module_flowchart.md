# Market Sentiment Module Flow Chart

```mermaid
flowchart TD
    A[Start] --> B[Fetch Reddit Posts]
    B --> B1[Preprocess Reddit Data]
    B1 --> C[Analyze Reddit Sentiment]
    C --> D[Fetch News Articles]
    D --> D1[Preprocess News Data]
    D1 --> E[Analyze News Sentiment using LLM]
    E --> F[Aggregate Sentiment Data]
    F --> G[Retrieve Market Indicators]
    G --> H[Calculate Unified Sentiment Score]
    H --> I[Persist Analysis Results to MongoDB]
    I --> J[Generate Trading Signals]
    J --> K[Send Notifications via Discord]
    K --> L[End]
```
