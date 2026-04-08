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

Also provides a lightweight `run_news_update()` path that:
- Fetches only *new* news articles since the last call.
- Fetches Reddit posts and checks for *new* posts since the last call.
- Re-scores sentiment when either new news OR new Reddit posts are found.
- Falls back to running full sentiment if both sources have new data.
- Keeps LLM token usage low by skipping the call when nothing is new.

Designed for traceability using LangSmith.
"""

import os
import time
import logging
from pydantic import BaseModel, Field
from tqdm import tqdm
from datetime import datetime, timezone
from typing import Dict, Any, List, Literal, Optional

from langsmith import traceable
from orbit.core.exception_manager import ExceptionManager
from orbit.market_intelligence.clients.reddit_client import RedditClient
from orbit.market_intelligence.clients.news_client import (
    fetch_news_articles,
    fetch_news_articles_since,
)
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
from orbit.utils.utils import require_env, get_indian_time, to_ist


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


class SentimentWorkflow(ExceptionManager):
    """
    End-to-end market sentiment analysis workflow.

    Responsibilities:
    - Fetch and analyze Reddit sentiment
    - Fetch and classify news sentiment using LLM
    - Retrieve macro market indicators
    - Combine multiple sentiment signals into a unified score
    - Persist analysis results to MongoDB
    - Generate trends and trading signals

    Lightweight path (run_news_update):
    - Fetches only *new* news articles since the last call.
    - Fetches Reddit posts and detects *new* posts since the last call.
    - Runs LLM sentiment when either source has new data.
    - Skips LLM entirely when nothing new is found, keeping token usage low.
    - Last-fetch timestamps are managed externally via Redis by Croner so
      they survive process restarts.
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

        # Timestamps managed here as in-process fallback.
        # Croner is responsible for persisting these to Redis across restarts.
        self._last_news_fetch: Optional[datetime] = None
        self._last_reddit_fetch: Optional[datetime] = None

        # Last news sentiment produced by any run — used for drift detection
        self.last_news_sentiment: Optional[NewsSentiment] = None

    # ------------------------------------------------------------------
    # TRACEABLE STEPS
    # ------------------------------------------------------------------

    @traceable(name="fetch_reddit_posts")
    def fetch_reddit(
        self,
        hours_back: int = 5,
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
        news_article: str = fetch_news_articles.invoke(topic)
        self._last_news_fetch = get_indian_time()
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

    def get_market_sentiments(self, news_text: str, prompt: Optional[str] = None) -> NewsSentiment:
        """
        Use LLM to classify overall market sentiment from news articles.

        Args:
            news_text: Combined news article text.

        Returns:
            Structured NewsSentiment object.
        """
        
        PROMPT = """
            Analyze overall market sentiment from the following news.

            Consider:
            - macroeconomic impact
            - gold reaction
            - crypto reaction
            - risk-on / risk-off tone
            - central bank signals

            News:
            {news_text}
            """ if prompt is None else prompt
        
        RETURN_FORMAT = """
            Respond ONLY in JSON:
            {
                "sentiment": "BULLISH | BEARISH | NEUTRAL",
                "confidence": 0.0,
                "explanation": "brief explanation"
            }
            """
        
        prompt = PROMPT.format(news_text=news_text) + "\n" + RETURN_FORMAT

        try:
            raw_content = self.llm.invoke(prompt)
            raw_content = str(raw_content)
            data = extract_json(raw_content)
            result = NewsSentiment(**data)
            self.last_news_sentiment = result
            return result

        except Exception as e:
            logger.exception("News sentiment analysis failed")
            self.handle_exception(
                exception=e,
                context_description="News sentiment analysis failed",
            )
            fallback = NewsSentiment(
                sentiment="NEUTRAL",
                confidence=0.3,
                explanation="Analysis failed",
            )
            self.last_news_sentiment = fallback
            return fallback

    def get_reasoning(
        self,
        posts: List[Dict[str, Any]],
        news_sentiment: NewsSentiment,
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
        except Exception as e:
            logger.exception("Reasoning generation failed")
            self.handle_exception(
                exception=e,
                context_description="Reasoning generation failed",
            )
            return "Market reasoning unavailable."

    # ------------------------------------------------------------------
    # REDDIT NEW-POST DETECTION
    # ------------------------------------------------------------------

    def _fetch_new_reddit_posts(
        self,
        since: Optional[datetime],
        hours_back: int = 1,
        posts_per_subreddit: int = 15,
    ) -> tuple[Dict[str, Dict[str, Any]], int]:
        """
        Fetch Reddit posts and filter to only those created after *since*.

        Args:
            since: Datetime threshold. Posts at or before this time are dropped.
                   When ``None`` all fetched posts are treated as new.
            hours_back: Look-back window passed to the Reddit client.
            posts_per_subreddit: Max posts per subreddit.

        Returns:
            Tuple of (filtered_reddit_posts_data, total_new_post_count).
            filtered_reddit_posts_data has the same shape as the raw fetch
            result but with only new posts inside each subreddit entry.
        """
        raw_data = self.fetch_reddit(
            hours_back=hours_back,
            posts_per_subreddit=posts_per_subreddit,
        )

        if since is None:
            total = sum(len(v.get("posts", [])) for v in raw_data.values())
            return raw_data, total

        filtered: Dict[str, Dict[str, Any]] = {}
        total_new = 0

        for subreddit, data in raw_data.items():
            new_posts = []
            for post in data.get("posts", []):
                # Reddit posts carry a UTC unix timestamp in "created_utc"
                created_utc = post.get("created_utc")
                if created_utc is not None:
                    post_dt = datetime.fromtimestamp(float(created_utc))
                    post_dt = to_ist(post_dt)
                    logger.info(f"Post '{post.get('title', '')}' created at {post_dt.isoformat()} (since={since.isoformat()})")
                    if post_dt <= since:
                        continue

                    if self._last_reddit_fetch is None or post_dt > self._last_reddit_fetch:
                        self._last_reddit_fetch = post_dt
                new_posts.append(post)

            if new_posts:
                filtered[subreddit] = {**data, "posts": new_posts}
                total_new += len(new_posts)

        return filtered, total_new

    # ------------------------------------------------------------------
    # LIGHTWEIGHT NEWS + REDDIT UPDATE
    # ------------------------------------------------------------------

    async def run_news_update(
        self,
        last_news_fetch: Optional[datetime] = None,
        last_reddit_fetch: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Lightweight update: check both news and Reddit for new content and
        re-score sentiment only when something new is found.

        This is designed to be called every 10 minutes.  It returns a
        minimal result dict so the caller can decide whether a full
        ``run_analysis()`` is warranted.

        Token usage is kept low because:
        - The LLM prompt is only sent when new articles or posts are found.
        - No macro indicator calls are made.

        Args:
            last_news_fetch: Timestamp of the last news fetch (from Redis).
                             Falls back to ``self._last_news_fetch`` when ``None``.
            last_reddit_fetch: Timestamp of the last Reddit fetch (from Redis).
                               Falls back to ``self._last_reddit_fetch`` when ``None``.

        Returns:
            Dict with keys:
                ``has_new_data`` (bool),
                ``has_new_articles`` (bool),
                ``has_new_reddit_posts`` (bool),
                ``news_sentiment`` (NewsSentiment | None),
                ``new_article_count`` (int),
                ``new_reddit_post_count`` (int),
                ``last_news_fetch`` (str ISO-8601),
                ``last_reddit_fetch`` (str ISO-8601),
                ``timestamp`` (str),
                ``success`` (bool).
        """
        # Resolve timestamps — prefer caller-supplied (from Redis) over in-process cache
        effective_news_since: Optional[datetime] = last_news_fetch or self._last_news_fetch
        effective_reddit_since: Optional[datetime] = last_reddit_fetch or self._last_reddit_fetch

        now = get_indian_time()

        logger.info(f"Last news fetch: {effective_news_since}, last Reddit fetch: {effective_reddit_since}")

        try:
            # ---- News ----
            news_text, new_ids, self._last_news_fetch = fetch_news_articles_since(since_aware=effective_news_since)
            has_new_articles = bool(news_text)
            new_article_count = len(new_ids)

            if not has_new_articles:
                logger.info("News update: no new articles found.")

            # ---- Reddit ----
            new_reddit_data, new_reddit_post_count = self._fetch_new_reddit_posts(
                since=effective_reddit_since,
                hours_back=1,
                posts_per_subreddit=15,
            )
            has_new_reddit_posts = new_reddit_post_count > 0

            if not has_new_reddit_posts:
                logger.info("News update: no new Reddit posts found.")

            has_new_data = has_new_articles or has_new_reddit_posts

            if not has_new_data:
                logger.info("News update: nothing new from news or Reddit — skipping LLM.")
                return {
                    "success": True,
                    "has_new_data": False,
                    "has_new_articles": False,
                    "has_new_reddit_posts": False,
                    "news_sentiment": None,
                    "new_article_count": 0,
                    "new_reddit_post_count": 0,
                    "last_news_fetch": self._last_news_fetch.isoformat() if self._last_news_fetch else None,
                    "last_reddit_fetch": self._last_reddit_fetch.isoformat() if self._last_reddit_fetch else None,
                    "timestamp": now.isoformat(),
                }

            logger.info(
                f"News update: {new_article_count} new articles, "
                f"{new_reddit_post_count} new Reddit posts — running LLM sentiment."
            )

            # ---- Build combined text for LLM ----
            combined_text_parts: List[str] = []

            if has_new_articles:
                combined_text_parts.append(f"[NEWS]\n{news_text}")

            if has_new_reddit_posts:
                reddit_snippets: List[str] = []
                for subreddit, data in new_reddit_data.items():
                    for post in data.get("posts", []):
                        title = post.get("title", "")
                        selftext = post.get("selftext", "")
                        snippet = f"{title}. {selftext}".strip()
                        if snippet:
                            reddit_snippets.append(snippet)
                if reddit_snippets:
                    combined_text_parts.append(
                        f"[REDDIT]\n" + "\n\n".join(reddit_snippets)
                    )

            combined_text = "\n\n".join(combined_text_parts)
            combined_text = "\n\n".join(combined_text_parts)

            prompt = f"""
            Analyze overall market sentiment from the following news and Reddit posts.

            Focus ONLY on MAJOR RECENT EVENTS that can move:
            - Financial markets
            - Gold (XAUUSD)
            - Crypto markets

            Prioritize:
            - Central bank decisions (Fed, ECB, BOJ, RBI)
            - Inflation data (CPI, PPI)
            - Interest rate changes
            - Geopolitical conflicts / wars
            - ETF approvals / regulations
            - Large institutional flows
            - USD strength / weakness
            - Recession signals
            - Liquidity changes

            Decision logic:
            - No major event → NEUTRAL
            - One strong major event → BULLISH or BEARISH
            - Multiple major events same direction → high confidence
            - Mixed major events → NEUTRAL

            Ignore:
            - opinions
            - technical analysis
            - minor commentary
            - speculation
            - duplicate news
            """

            news_sentiment = self.get_market_sentiments(combined_text, prompt=prompt)

            return {
                "success": True,
                "has_new_data": True,
                "has_new_articles": has_new_articles,
                "has_new_reddit_posts": has_new_reddit_posts,
                "news_sentiment": news_sentiment,
                "new_article_count": new_article_count,
                "new_reddit_post_count": new_reddit_post_count,
                "last_news_fetch": self._last_news_fetch.isoformat() if self._last_news_fetch else None,
                "last_reddit_fetch": self._last_reddit_fetch.isoformat() if self._last_reddit_fetch else None,
                "timestamp": now.isoformat(),
            }

        except Exception as e:
            logger.exception("run_news_update failed")
            self.handle_exception(
                exception=e,
                context_description="run_news_update",)
            return {
                "success": False,
                "has_new_data": False,
                "has_new_articles": False,
                "has_new_reddit_posts": False,
                "news_sentiment": None,
                "new_article_count": 0,
                "new_reddit_post_count": 0,
                "error": str(e),
                "last_news_fetch": self._last_news_fetch.isoformat() if self._last_news_fetch else None,
                "last_reddit_fetch": self._last_reddit_fetch.isoformat() if self._last_reddit_fetch else None,
                "timestamp": now.isoformat(),
            }

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
                sum(s["overall_score"] for s in historical_sentiment) / len(historical_sentiment)
                if historical_sentiment
                else 0
            )

            reasoning: str = self.get_reasoning(top_posts, news_sentiment)

            combined_result = self._combine_results(
                reddit_result,
                news_sentiment,
                indicators,
                historical_score=historical_score,
            )

            record_id = self._save_to_database(
                reddit_result,
                top_posts,
                news_text,
                indicators,
                combined_result,
                int((time.time() - start_time) * 1000),
            )

            # Update in-process timestamps after a successful full run
            now = get_indian_time()
            self._last_news_fetch = now
            self._last_reddit_fetch = now

            trend = self.mongodb.calculate_trends(hours=24)
            signal = self.mongodb.get_trading_signals()

            return {
                "success": True,
                "timestamp": get_indian_time().isoformat(),
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
            self.handle_exception(
                exception=e,
                context_description="run_analysis",)    
            return {
                "success": False,
                "error": str(e),
                "timestamp": get_indian_time().isoformat(),
            }

    # ------------------------------------------------------------------
    # INTERNAL HELPERS
    # ------------------------------------------------------------------

    def _combine_results(
        self,
        reddit_result: Dict[str, Any],
        news_sentiment: NewsSentiment,
        indicators: MarketIndicators,
        historical_score: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Combine Reddit, News, Indicator signals and historical score into a
        final sentiment score.

        Returns:
            Dictionary with combined score, label, and confidence.
        """
        news_weight = 0.4
        reddit_weight = 0.3
        historical_weight = 0.2
        vix_weight = 0.05
        fear_greed_weight = 0.05

        fear_greed = indicators.fear_greed_index
        fear_greed_score: float = 0

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
            + (indicators.vix or 0) * vix_weight * -1
            + fear_greed_score
        )

        if combined_score > 0.2:
            label = "BULLISH"
        elif combined_score < -0.2:
            label = "BEARISH"
        else:
            label = "NEUTRAL"

        return {
            "score": round(combined_score, 3),
            "sentiment": label,
            "confidence": round(
                reddit_result["confidence"] * reddit_weight
                + news_sentiment.confidence * news_weight,
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
