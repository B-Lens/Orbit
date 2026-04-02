"""
Sentiment Workflow Module

This module orchestrates the full market sentiment analysis pipeline by:
- Fetching Reddit posts
- Applying weighted Reddit sentiment analysis
- Fetching crypto/market news
- Running LLM-based news sentiment classification
- Fetching macro indicators (VIX, Fear & Greed)
- Combining all signals into a unified market sentiment score
- Persisting results to MongoDB
- Producing trading signals and trends

Designed for traceability using LangSmith.
"""

import os
import time
import logging
from pydantic import BaseModel, Field
from tqdm import tqdm
from datetime import datetime
from typing import Dict, Any, List, Literal

from langsmith import traceable

from orbit.market_intelligence.clients.reddit_client import RedditClient
from orbit.market_intelligence.clients.news_client import fetch_news_articles
from orbit.market_intelligence.analysis.reddit_sentiment import (
    RedditSentimentEntry,
    WeightedRedditAnalyzer,
    extract_json,
)
from orbit.market_intelligence.models.mongodb_models import (
    MongoDBManager,
    SentimentRecord,
)
from orbit.market_intelligence.llm.llm_endpoint import LLM
from orbit.market_intelligence.utils.utils import (
    fetch_market_indicators,
    SentimentType,
    MarketIndicators,
)
from orbit.utils.utils import require_env


# ---- LangSmith env ----
os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGCHAIN_API_KEY"] = require_env("LANGSMITH_API_KEY")

BATCH_SIZE = 100
logger = logging.getLogger("Orbit")


class NewsSentiment(BaseModel):
    """
    Structured representation of LLM-evaluated news sentiment.

    Attributes:
        sentiment: Overall market sentiment (BULLISH, BEARISH, NEUTRAL)
        confidence: Confidence score between 0 and 1
        explanation: Brief textual explanation of reasoning
    """
    sentiment: Literal["BULLISH", "BEARISH", "NEUTRAL"]
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str


class SentimentWorkflow:
    """
    End-to-end market sentiment analysis workflow.

    Responsibilities:
    - Fetch and analyze Reddit sentiment
    - Fetch and classify news sentiment using LLM
    - Retrieve macro market indicators
    - Combine multiple sentiment signals into a unified score
    - Persist analysis results to MongoDB
    - Generate trends and trading signals
    """

    def __init__(self, llm: LLM) -> None:
        """
        Initialize workflow dependencies.

        Args:
            llm: LLM wrapper used for news sentiment and reasoning generation.
        """
        self.llm: LLM = llm
        self.reddit_client: RedditClient = RedditClient()
        self.reddit_analyzer: WeightedRedditAnalyzer = WeightedRedditAnalyzer(llm)
        self.mongodb: MongoDBManager = MongoDBManager()

    # ------------------------------------------------------------------
    # TRACEABLE STEPS
    # ------------------------------------------------------------------

    @traceable(name="fetch_reddit_posts")
    def fetch_reddit(
        self,
        hours_back: int = 6,
        posts_per_subreddit: int = 15,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Fetch weighted Reddit posts from configured subreddits.

        Args:
            hours_back: Time window in hours to look back.
            posts_per_subreddit: Maximum posts per subreddit.

        Returns:
            Dictionary mapping subreddit name to its fetched post data.
        """
        return self.reddit_client.fetch_weighted_posts(
            hours_back=hours_back,
            posts_per_subreddit=posts_per_subreddit,
        )

    @traceable(name="calculate_dynamic_weights")
    def calculate_weights(
        self,
        reddit_posts_data: Dict[str, Dict[str, Any]],
    ) -> Dict[str, float]:
        """
        Calculate dynamic weights for each subreddit.

        Args:
            reddit_posts_data: Raw Reddit post data.

        Returns:
            Dictionary mapping subreddit to computed weight.
        """
        return self.reddit_client.calculate_dynamic_weights(
            reddit_posts_data
        )

    @traceable(name="aggregate_reddit_sentiment")
    def aggregate_sentiment(
        self,
        sentiments: List[RedditSentimentEntry],
    ) -> Dict[str, Any]:
        """
        Aggregate weighted Reddit sentiment entries.

        Args:
            sentiments: List of analyzed Reddit sentiment entries.

        Returns:
            Aggregated sentiment dictionary including score, label, confidence.
        """
        return self.reddit_analyzer.aggregate_weighted_sentiment(sentiments)

    @traceable(name="fetch_news")
    def fetch_news(self, topic: str = "crypto market") -> str:
        """
        Fetch news articles related to a given topic.

        Args:
            topic: Topic to search news for.

        Returns:
            Combined news text.
        """
        news_article:str = fetch_news_articles.invoke(topic)
        return news_article

    @traceable(name="fetch_indicators")
    def fetch_indicators(self) -> MarketIndicators:
        """
        Fetch macro market indicators such as VIX and Fear & Greed index.

        Returns:
            MarketIndicators object.
        """
        return fetch_market_indicators()

    # ------------------------------------------------------------------
    # LLM SENTIMENT
    # ------------------------------------------------------------------

    def get_market_sentiments(self, news_text: str) -> NewsSentiment:
        """
        Use LLM to classify overall market sentiment from news articles.

        Args:
            news_text: Combined news article text.

        Returns:
            Structured NewsSentiment object.
        """
        prompt = f"""
        Analyze overall market sentiment from the following news:
        {news_text}

        Respond in JSON:
        {{
            "sentiment": "BULLISH|BEARISH|NEUTRAL",
            "confidence": 0.0-1.0,
            "explanation": "brief explanation"
        }}
        """

        try:
            raw_content = self.llm.invoke(prompt)
            raw_content = str(raw_content)
            data = extract_json(raw_content)
            return NewsSentiment(**data)

        except Exception:
            logger.exception("News sentiment analysis failed")
            return NewsSentiment(
                sentiment="NEUTRAL",
                confidence=0.3,
                explanation="Analysis failed"
            )

    def get_reasoning(
        self,
        posts: List[Dict[str, Any]],
        news_sentiment: NewsSentiment
    ) -> str:
        """
        Generate LLM-based reasoning for final market sentiment.

        Args:
            posts: Top influential Reddit posts.
            news_sentiment: Structured news sentiment result.

        Returns:
            Human-readable reasoning string.
        """
        posts_summary = "\n".join(p["explanation"] for p in posts)

        prompt = f"""
        Provide reasoning for overall market sentiment:

        Reddit Analysis:
        {posts_summary}

        News Sentiment:
        {news_sentiment.explanation}
        """

        try:
            content = self.llm.invoke(prompt)
            return str(content).strip()
        except Exception:
            logger.exception("Reasoning generation failed")
            return "Market reasoning unavailable."

    # ------------------------------------------------------------------
    # MAIN WORKFLOW
    # ------------------------------------------------------------------

    async def run_analysis(self) -> Dict[str, Any]:
        """
        Execute the complete sentiment analysis pipeline.

        Steps:
        1. Fetch Reddit posts
        2. Analyze and aggregate Reddit sentiment
        3. Fetch news and indicators
        4. Compute combined sentiment score
        5. Persist results
        6. Return structured result

        Returns:
            Dictionary containing final sentiment analysis results.
        """
        start_time = time.time()

        try:
            reddit_posts_data = self.fetch_reddit()
            dynamic_weights = self.calculate_weights(reddit_posts_data)

            all_sentiments: List[RedditSentimentEntry] = []

            for subreddit_name, data in tqdm(reddit_posts_data.items()):
                weight = dynamic_weights.get(subreddit_name, 0.5)
                posts = data["posts"]

                batch_id = 1
                for i in range(0, len(posts), BATCH_SIZE):
                    batch = posts[i:i + BATCH_SIZE]
                    sentiments = self.reddit_analyzer.analyze_batch_sentiment(
                        batch_id, batch, weight
                    )
                    all_sentiments.append(sentiments)
                    batch_id += 1

            reddit_result = self.aggregate_sentiment(all_sentiments)
            top_posts = self.reddit_analyzer.get_top_influential_posts(
                all_sentiments
            )

            news_text = self.fetch_news()
            indicators = self.fetch_indicators()
            news_sentiment = self.get_market_sentiments(news_text)

            historical_sentiment: List[Dict[str, Any]] = self.mongodb.get_recent_sentiments(hours=24)
            historical_score: float = (
                sum(s["overall_score"] for s in historical_sentiment) / len(historical_sentiment) if historical_sentiment else 0
            )

            reasoning: str = self.get_reasoning(top_posts, news_sentiment)

            combined_result = self._combine_results(
                reddit_result,
                news_sentiment,
                indicators,
                historical_score=historical_score
            )

            record_id = self._save_to_database(
                reddit_result,
                top_posts,
                news_text,
                indicators,
                combined_result,
                int((time.time() - start_time) * 1000),
            )

            trend = self.mongodb.calculate_trends(hours=24)
            signal = self.mongodb.get_trading_signals()

            return {
                "success": True,
                "timestamp": datetime.now().isoformat(),
                "database_id": record_id,
                **combined_result,
                "reasoning": reasoning,
                "trends": trend.dict() if trend else None,
                "trading_signal": signal,
                "processing_time_ms": int(
                    (time.time() - start_time) * 1000
                ),
            }

        except Exception as e:
            logger.exception("Workflow failed")
            return {
                "success": False,
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            }

    # ------------------------------------------------------------------
    # INTERNAL HELPERS
    # ------------------------------------------------------------------

    def _combine_results(
        self,
        reddit_result: Dict[str, Any],
        news_sentiment: NewsSentiment,
        indicators: MarketIndicators,
        historical_score: float = 0.0, # Placeholder for historical data
    ) -> Dict[str, Any]:
        """
        Combine Reddit, News, Indicator signals and historical score into a final sentiment score.

        Returns:
            Dictionary with combined score, label, and confidence.
        """
        news_weight = 0.4
        reddit_weight = 0.3
        historical_weight = 0.2
        vix_weight = 0.05
        fear_greed_weight = 0.05

        fear_greed = indicators.fear_greed_index
        fear_greed_score:float = 0

        if fear_greed is not None:
            direction = 1 if fear_greed < 50 else -1
            fear_greed_score = fear_greed * fear_greed_weight * direction

        combined_score = (
            reddit_result["overall_score"] * reddit_weight
            + news_sentiment.confidence
            * news_weight
            * (
                1
                if news_sentiment.sentiment == SentimentType.BULLISH
                else -1
                if news_sentiment.sentiment == SentimentType.BEARISH
                else 0
            )
            + historical_score * historical_weight
            + (indicators.vix or 0) * vix_weight * -1  # Higher VIX = more bearish
            + fear_greed_score)

        if combined_score > 0.2:
            label = "BULLISH"
        elif combined_score < -0.2:
            label = "BEARISH"
        else:
            label = "NEUTRAL"

        return {
            "score": round(combined_score, 3),
            "sentiment": label,
            "confidence": round(reddit_result["confidence"] * reddit_weight + news_sentiment.confidence * news_weight, 2),
        }

    def _save_to_database(
        self,
        reddit_result: Dict[str, Any],
        top_posts: List[Dict[str, Any]],
        news_text: str,
        indicators: MarketIndicators,
        combined: Dict[str, Any],
        processing_time: int,
    ) -> str:
        """
        Persist sentiment analysis record to MongoDB.

        Returns:
            Inserted record ID.
        """
        record = SentimentRecord(
            overall_score=combined["score"],
            sentiment_label=combined["sentiment"],
            confidence=combined["confidence"],
            reddit_weighted_score=reddit_result["overall_score"],
            reddit_category_breakdown=reddit_result["category_breakdown"],
            reddit_posts_analyzed=reddit_result["total_posts_analyzed"],
            top_influential_posts=top_posts,
            news_sentiment={
                "summary": news_text[:500] if news_text else "",
                "source": "newsdata.io",
            },
            market_indicators={
                "vix": indicators.vix,
                "fear_greed_index": indicators.fear_greed_index,
            },
            processing_time_ms=processing_time,
        )

        return self.mongodb.save_sentiment(record)
