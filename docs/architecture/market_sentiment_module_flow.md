# Market Sentiment Module Flow

## Summary

The Market Intelligence module performs end-to-end crypto sentiment analysis by combining Reddit community sentiment, LLM-classified news sentiment, and macro market indicators (VIX, Fear & Greed Index) into a unified sentiment score. It is orchestrated by `SentimentWorkflow`, scheduled by `Croner`, and backed by two LLM providers (Groq primary, OpenRouter fallback). Results are persisted to MongoDB and surfaced as trading signals consumed by `SignalAnalyzer`.

Key characteristics:
- **Dual LLM strategy**: `LLM` (Groq) is the primary inference engine; `OpenRouterClient` is the automatic fallback.
- **Weighted Reddit analysis**: Each subreddit carries a configurable static weight plus a dynamic weight derived from post count and average score.
- **Sentiment drift detection**: `Croner` compares the latest sentiment score against a cached Redis value and fires a Discord alert when the drift exceeds `SENTIMENT_DRIFT_THRESHOLD`.
- **Two scheduling cadences**: full analysis runs hourly; news + Reddit updates run every `NEWS_POLL_INTERVAL_SECONDS` (≈10 min).
- **Exception handling**: every class inherits `ExceptionManager` → `DiscordManager`, so all unhandled errors are reported to Discord automatically.

---

## Diagram

```mermaid
flowchart TD

    subgraph Scheduling ["Scheduling — Croner (sentimen_cron.py)"]
        CR1[Croner.__init__]
        CR2[start: spawn hourly_thread + news_thread]
        CR3["hourly_thread\n(every 60 min)"]
        CR4["news_thread\n(every NEWS_POLL_INTERVAL_SECONDS ~10 min)"]
        CR5[run_full_analysis]
        CR6[run_news_update]
        CR7{Sentiment drift\n> threshold?}
        CR8[Send Discord drift alert]
        CR9[Cache score in Redis\nsentiment:latest_score]
    end

    subgraph LLM_Layer ["LLM Layer"]
        LLM1["LLM (llm_endpoint.py)\nGroq primary"]
        LLM2["OpenRouterClient (openrouter_client.py)\nfallback"]
        LLM1 -->|invoke fails| LLM2
    end

    subgraph Reddit ["Reddit Pipeline"]
        R1[RedditClient\npraw.Reddit]
        R2["WeightedSubreddit config\n(reddit_config.py)"]
        R3[Fetch posts per subreddit]
        R4["calculate_dynamic_weight\n(posts_count, avg_score)"]
        R5[WeightedRedditAnalyzer\n.analyze]
        R6[LLM prompt → RedditSentimentEntry]
        R7[extract_json → validated Pydantic model]
        R8[Weighted aggregate score per subreddit]
    end

    subgraph News ["News Pipeline"]
        N1[Fetch news articles\nexternal source]
        N2[Preprocess / truncate text]
        N3["SentimentWorkflow\n.get_market_sentiments"]
        N4[Build LLM prompt]
        N5[LLM.invoke → raw JSON]
        N6[Parse → NewsSentiment Pydantic model]
    end

    subgraph Indicators ["Macro Indicators (utils.py)"]
        I1["fetch_vix_index\n(lru_cache, time_bucket)"]
        I2["fetch_crypto_fear_greed\n(lru_cache, time_bucket)"]
        I3[MarketIndicators model]
    end

    subgraph Workflow ["SentimentWorkflow (sentimental_workflow.py)"]
        W1[SentimentWorkflow.__init__\nreceives LLM instance]
        W2[Aggregate Reddit score]
        W3[Aggregate News score]
        W4[Retrieve MarketIndicators]
        W5[Calculate unified sentiment score]
        W6[Build SentimentResult]
    end

    subgraph Persistence ["Persistence — MongoDBManager (mongodb_models.py)"]
        DB1[MongoDBManager.__init__\nsentiment_history + trends collections]
        DB2[store SentimentResult]
        DB3[get_recent_sentiments\nhours / limit]
        DB4[get_sentiment_by_date_range]
        DB5[Generate SentimentTrend]
    end

    subgraph Outputs ["Outputs"]
        O1[Trading signal dict\nyielded to SignalAnalyzer]
        O2[Discord notification\nvia ExceptionManager / DiscordManager]
        O3[Logged metrics & alerts]
    end

    subgraph ErrorHandling ["Cross-cutting — ExceptionManager → DiscordManager"]
        E1[clientExceptionHandler]
        E2[handle_exception]
        E3[exception_trigger → Discord webhook]
    end

    %% Scheduling flow
    CR1 --> CR2
    CR2 --> CR3
    CR2 --> CR4
    CR3 --> CR5
    CR4 --> CR6
    CR5 --> W1
    CR6 --> W1

    %% LLM wiring
    W1 --> LLM1

    %% Reddit pipeline
    CR5 --> R1
    R1 --> R3
    R2 --> R4
    R3 --> R4
    R4 --> R5
    R5 --> R6
    R6 --> LLM1
    LLM1 --> R7
    R7 --> R8
    R8 --> W2

    %% News pipeline
    CR5 --> N1
    CR6 --> N1
    N1 --> N2
    N2 --> N3
    N3 --> N4
    N4 --> LLM1
    LLM1 --> N5
    N5 --> N6
    N6 --> W3

    %% Indicators
    CR5 --> I1
    CR5 --> I2
    I1 --> I3
    I2 --> I3
    I3 --> W4

    %% Workflow aggregation
    W2 --> W5
    W3 --> W5
    W4 --> W5
    W5 --> W6

    %% Persistence
    W6 --> DB2
    DB2 --> DB1
    DB1 --> DB3
    DB1 --> DB4
    DB3 --> DB5
    DB4 --> DB5

    %% Drift detection
    W6 --> CR7
    CR7 -->|yes| CR8
    CR7 -->|no| CR9
    CR8 --> CR9

    %% Outputs
    DB5 --> O1
    CR8 --> O2
    W6 --> O3

    %% Error handling (cross-cutting)
    LLM1 -.->|exception| E1
    W1 -.->|exception| E2
    CR5 -.->|exception| E2
    E1 --> E3
    E2 --> E3
    E3 --> O2
```
