# workflow.py
import os
import time
import logging
from tqdm import tqdm
from datetime import datetime
from typing import Dict, Any, List

from langsmith import traceable
from langchain_core.language_models.chat_models import BaseChatModel

from orbit.ai.clients.reddit_client import RedditClient
from orbit.ai.clients.news_client import fetch_news_articles
from orbit.ai.analysis.reddit_sentiment import RedditSentimentEntry, WeightedRedditAnalyzer
from orbit.ai.models.mongodb_models import MongoDBManager, SentimentRecord
from orbit.ai.utils.utils import (
    fetch_market_indicators,
    parse_sentiment,
    SentimentType,
    MarketIndicators,
)
from orbit.utils.utils import require_env


# ---- LangSmith env ----
os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGCHAIN_API_KEY"] = require_env("LANGSMITH_API_KEY")

logger = logging.getLogger("Orbit")


class SentimentWorkflow:
    def __init__(self, llm: BaseChatModel) -> None:
        self.llm: BaseChatModel = llm
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
        return self.reddit_client.fetch_weighted_posts(
            hours_back=hours_back,
            posts_per_subreddit=posts_per_subreddit,
        )

    @traceable(name="calculate_dynamic_weights")
    def calculate_weights(
        self,
        reddit_posts_data: Dict[str, Dict[str, Any]],
    ) -> Dict[str, float]:
        return self.reddit_client.calculate_dynamic_weights(
            reddit_posts_data
        )

    @traceable(name="aggregate_reddit_sentiment")
    def aggregate_sentiment(
        self,
        sentiments: List[RedditSentimentEntry],
    ) -> Dict[str, Any]:
        return self.reddit_analyzer.aggregate_weighted_sentiment(sentiments)

    @traceable(name="fetch_news")
    def fetch_news(self, topic: str = "crypto market") -> str:
        result: str = fetch_news_articles.invoke(topic)
        return result

    @traceable(name="fetch_indicators")
    def fetch_indicators(self) -> MarketIndicators:
        return fetch_market_indicators()

    @traceable(name="save_to_mongodb")
    def save_db(
        self,
        reddit_result: Dict[str, Any],
        top_posts: List[Dict[str, Any]],
        news_text: str,
        indicators: MarketIndicators,
        combined: Dict[str, Any],
        processing_time: int,
    ) -> str:
        return self._save_to_database(
            reddit_result,
            top_posts,
            news_text,
            indicators,
            combined,
            processing_time,
        )

    # ------------------------------------------------------------------
    # MAIN WORKFLOW
    # ------------------------------------------------------------------

    async def run_analysis(self) -> Dict[str, Any]:
        start_time = time.time()

        try:
            logger.info("Fetching weighted Reddit posts...")
            reddit_posts_data = self.fetch_reddit()

            logger.info("Calculating dynamic weights...")
            dynamic_weights = self.calculate_weights(reddit_posts_data)

            logger.info("Analyzing Reddit posts...")
            all_sentiments: List[RedditSentimentEntry] = []

            for subreddit_name, data in tqdm(reddit_posts_data.items()):
                weight = dynamic_weights.get(subreddit_name, 0.5)

                for post in tqdm(data["posts"]):
                    sentiment = await self.reddit_analyzer.analyze_post_sentiment(
                        post, weight
                    )
                    all_sentiments.append(sentiment)

            logger.info("Aggregating weighted sentiments...")
            reddit_result = self.aggregate_sentiment(all_sentiments)

            top_posts = self.reddit_analyzer.get_top_influential_posts(
                all_sentiments
            )

            logger.info("Running news + indicator analysis...")
            news_text = self.fetch_news()
            indicators = self.fetch_indicators()

            combined_result = self._combine_results(
                reddit_result,
                news_text,
                indicators,
            )

            logger.info("Saving to MongoDB...")
            record_id = self.save_db(
                reddit_result=reddit_result,
                top_posts=top_posts,
                news_text=news_text,
                indicators=indicators,
                combined=combined_result,
                processing_time=int((time.time() - start_time) * 1000),
            )

            trend = self.mongodb.calculate_trends(hours=24)
            signal = self.mongodb.get_trading_signals()

            final_result: Dict[str, Any] = {
                "success": True,
                "timestamp": datetime.now().isoformat(),
                "database_id": record_id,
                "sentiment": combined_result,
                "reddit_analysis": {
                    "weighted_score": reddit_result["overall_score"],
                    "label": reddit_result["sentiment_label"],
                    "confidence": reddit_result["confidence"],
                    "posts_analyzed": reddit_result["total_posts_analyzed"],
                    "category_breakdown": reddit_result["category_breakdown"],
                },
                "market_indicators": {
                    "vix": indicators.vix,
                    "fear_greed_index": indicators.fear_greed_index,
                },
                "trends": trend.dict() if trend else None,
                "trading_signal": signal,
                "processing_time_ms": int(
                    (time.time() - start_time) * 1000
                ),
            }

            logger.info(
                "Analysis complete. Final sentiment: %s",
                combined_result["label"],
            )

            logger.info("Final result: %s", final_result)

            return final_result

        except Exception as e:
            logger.error("Enhanced workflow failed: %s", e)
            import traceback

            logger.error(traceback.format_exc())

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
        news_text: str,
        indicators: MarketIndicators,
    ) -> Dict[str, Any]:

        news_sentiment = parse_sentiment(news_text)

        reddit_weight = 0.6
        news_weight = 0.4

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
        )

        if combined_score > 0.2:
            label = "BULLISH"
        elif combined_score < -0.2:
            label = "BEARISH"
        else:
            label = "NEUTRAL"

        return {
            "score": round(combined_score, 3),
            "label": label,
            "confidence": round(
                (
                    reddit_result["confidence"] * reddit_weight
                    + news_sentiment.confidence * news_weight
                ),
                2,
            ),
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

        record = SentimentRecord(
            overall_score=combined["score"],
            sentiment_label=combined["label"],
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
