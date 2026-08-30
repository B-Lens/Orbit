"""
Sentiment Workflow Module

This module orchestrates the full market sentiment analysis pipeline by:
- Fetching Reddit posts
- Applying chunk-then-synthesise Reddit sentiment analysis (no per-post aggregation)
- Fetching financial tweets via the twitter CLI tool
- Applying LLM-based Twitter sentiment analysis (chunk-then-synthesise)
- Fetching crypto/market news via RSS feeds (macro, crypto, global, finance)
- Running LLM-based news sentiment classification
- Fetching macro indicators (VIX, Fear & Greed)
- Combining all signals into a unified market sentiment score
- Persisting results to MongoDB
- Producing trading signals and trends

Signal weight priority (highest → lowest):
    Twitter  > News RSS  > Reddit  > Historical  > Indicators

Also provides a lightweight `run_news_update()` path that:
- Fetches only *new* RSS articles since the last call.
- Fetches Reddit posts and checks for *new* posts since the last call.
- Fetches only *new* tweets since the last call.
- Re-scores sentiment when any source has new data.
- Keeps LLM token usage low by skipping the call when nothing is new.

"""

import time
import logging
from pydantic import BaseModel, Field, field_validator
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from zoneinfo import ZoneInfo


from orbit.core.exception_manager import ExceptionManager
from orbit.market_intelligence.clients.reddit_client import RedditClient
from orbit.market_intelligence.clients.twitter_client import TwitterClient
from orbit.market_intelligence.clients.rss_client import (
    fetch_all_rss_news,
    deduplicate,
    sort_latest,
    filter_recent,
    format_for_llm,
)
from orbit.market_intelligence.analysis.reddit_sentiment import (
    WeightedRedditAnalyzer,
    RedditOverallResult,
)
from orbit.market_intelligence.analysis.twitter_sentiment import (
    TwitterSentimentAnalyzer,
    TwitterSentimentResult,
    ChunkSentimentSummary,
)
from orbit.market_intelligence.models.mongodb_models import (
    MongoDBManager,
    SentimentRecord,
)
from orbit.market_intelligence.llm.llm_endpoint import LLM
from orbit.market_intelligence.llm.prompt_manager import PromptManager
from orbit.market_intelligence.utils.utils import (
    fetch_market_indicators,
    SentimentType,
    MarketIndicators,
)
from orbit.utils.utils import extract_json, get_indian_time, to_ist


logger = logging.getLogger("Orbit")

# ---------------------------------------------------------------------------
# Sentiment combination weights
# ---------------------------------------------------------------------------
weight_dict = {
    "twitter": 0.35,
    "news": 0.30,
    "reddit": 0.20,
    "historical": 0.10,
    "vix": 0.025,
    "fear_greed": 0.025
}

class Sentiment(BaseModel):
    """
    Structured representation of LLM-evaluated news sentiment.

    Attributes:
        sentiment: Overall market sentiment (BULLISH, BEARISH, NEUTRAL)
        confidence: Confidence score between 0 and 1
        explanation: Brief textual explanation of reasoning
    """
    sentiment: SentimentType
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str

class NewsSentiment(BaseModel):
    """
    Structured representation of LLM-evaluated news sentiment.

    Attributes:
        sentiment: Overall market sentiment (BULLISH, BEARISH, NEUTRAL)
        confidence: Confidence score between 0 and 1
        explanation: Brief textual explanation of reasoning
    """
    sentiment: SentimentType
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str


class WebSearchSentiment(Sentiment):
    """Validated half-hourly sentiment grounded in live web sources."""

    sources: List[str] = Field(min_length=1, max_length=8)

    @field_validator("sources")
    @classmethod
    def validate_sources(cls, sources: List[str]) -> List[str]:
        unique_sources = list(dict.fromkeys(sources))
        if any(not source.startswith(("https://", "http://")) for source in unique_sources):
            raise ValueError("web-search sources must be HTTP URLs")
        return unique_sources


class SentimentWorkflow(ExceptionManager):
    """
    End-to-end market sentiment analysis workflow.

    Responsibilities:
    - Fetch and analyze Reddit sentiment (chunk-then-synthesise, no per-post aggregation)
    - Fetch and analyze Twitter/X financial tweets (highest weight)
    - Fetch and classify news sentiment using LLM (sourced from RSS feeds)
    - Retrieve macro market indicators
    - Combine multiple sentiment signals into a unified score
    - Persist analysis results to MongoDB
    - Generate trends and trading signals

    Signal weight priority (highest → lowest):
        Twitter (0.35) > News RSS (0.30) > Reddit (0.20) >
        Historical (0.10) > VIX + Fear&Greed (0.05 combined)

    Lightweight path (run_news_update):
    - Fetches only *new* RSS articles since the last call.
    - Fetches Reddit posts and detects *new* posts since the last call.
    - Fetches only *new* tweets since the last call.
    - Runs LLM sentiment when any source has new data.
    - Skips LLM entirely when nothing new is found, keeping token usage low.
    - Last-fetch timestamps are managed externally via Redis by Croner so
      they survive process restarts.

    RSS feed categories covered:
    - macro  : Investing.com (most popular, stock market, forex)
    - crypto : CoinDesk, CoinTelegraph
    - global : BBC World, CNN World
    - finance: Yahoo Finance Gold (GC=F), Yahoo Finance USD Index (DX-Y.NYB)
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
        self.twitter_client: TwitterClient = TwitterClient()
        self.twitter_analyzer: TwitterSentimentAnalyzer = TwitterSentimentAnalyzer(llm)
        self.mongodb: MongoDBManager = MongoDBManager()
        self.prompt_manager = PromptManager()

        # Timestamps managed here as in-process fallback.
        # Croner is responsible for persisting these to Redis across restarts.
        self._last_news_fetch: Optional[datetime] = None
        self._last_reddit_fetch: Optional[datetime] = None
        self._last_twitter_fetch: Optional[datetime] = None

        # Last news sentiment produced by any run — used for drift detection
        self.last_news_sentiment: Optional[NewsSentiment] = None

    # ------------------------------------------------------------------
    # FETCH STEPS
    # ------------------------------------------------------------------

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

    def analyze_reddit(
        self,
        reddit_posts_data: Dict[str, Dict[str, Any]],
    ) -> RedditOverallResult:
        """
        Run chunk-then-synthesise LLM sentiment analysis over all Reddit posts.

        All posts from all subreddits are flattened, split into token-safe
        chunks, each chunk gets an overall sentiment from the LLM, and the
        chunk summaries are synthesised into a single :class:`RedditOverallResult`.

        Args:
            reddit_posts_data: Raw Reddit post data keyed by subreddit name.

        Returns:
            :class:`RedditOverallResult` with final synthesised sentiment.
        """
        result = self.reddit_analyzer.analyze(reddit_posts_data)
        logger.info(
            f"Reddit sentiment: {result.sentiment} "
            f"(confidence={result.confidence}, "
            f"posts={result.total_posts_analyzed}, "
            f"chunks={result.chunks_analyzed})"
        )
        return result

    def fetch_twitter(self) -> List[Dict[str, Any]]:
        """
        Fetch financial tweets from all configured Twitter search queries.

        Returns:
            Deduplicated list of enriched tweet dicts.
        """
        tweets = self.twitter_client.fetch_tweets()
        logger.info(f"Twitter: fetched {len(tweets)} tweets (full run)")
        return tweets

    def analyze_twitter(
        self, tweets: List[Dict[str, Any]]
    ) -> TwitterSentimentResult:
        """
        Run LLM-based sentiment analysis on tweets using chunk-then-synthesise.

        Args:
            tweets: Enriched tweet dicts from :meth:`fetch_twitter`.

        Returns:
            :class:`TwitterSentimentResult` with final synthesised sentiment.
        """
        summaries: List[ChunkSentimentSummary] = self.twitter_analyzer.analyze_tweets(tweets)
        result = self.twitter_analyzer.aggregate(summaries, total_tweets=len(tweets))
        logger.info(
            f"Twitter sentiment: {result.sentiment} "
            f"(confidence={result.confidence}, score={result.overall_score})"
        )
        return result

    def fetch_news(self, hours_back: int = 4, limit: int = 30) -> str:
        """
        Fetch recent news articles from all configured RSS feeds.

        Args:
            hours_back: Only include articles published within this many hours.
            limit: Maximum number of articles to include in the formatted output.

        Returns:
            Combined news text formatted for LLM consumption.
        """
        news = fetch_all_rss_news()
        news = deduplicate(news)
        news = sort_latest(news)
        news = filter_recent(news, hours=hours_back)
        news_text = format_for_llm(news, limit=limit)

        self._last_news_fetch = get_indian_time()
        logger.info(f"RSS news fetch: {len(news)} articles after dedup/filter (limit={limit})")
        return news_text

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
            news_text: Formatted text containing recent news articles.

        Returns:
            Structured NewsSentiment object.
        """

        base_prompt = self.prompt_manager.get_prompt("news_sentiment", news_text=news_text)

        full_prompt = base_prompt

        try:
            raw_content = self.llm.invoke(full_prompt)
            raw_content = str(raw_content)
            logger.info(f"LLM raw output for news sentiment: {raw_content}")
            data = extract_json(raw_content)
            # extract_json may return a list if the LLM output is a JSON array.
            # In that case, take the first element if it is a dict.
            if isinstance(data, list):
                if len(data) > 0 and isinstance(data[0], dict):
                    data = data[0]
                else:
                    raise ValueError("LLM returned a list without a valid dict element")
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
                sentiment=SentimentType.NEUTRAL,
                confidence=0.3,
                explanation="Analysis failed",
            )
            self.last_news_sentiment = fallback
            return fallback

    def get_reasoning(
        self,
        reddit_result: RedditOverallResult,
        news_sentiment: NewsSentiment,
        twitter_result: TwitterSentimentResult,
        indicators: MarketIndicators,
    ) -> str:
        """
        Generate LLM-based reasoning for final market sentiment.

        Args:
            reddit_result: Overall Reddit sentiment result.
            news_sentiment: Structured news sentiment result.
            twitter_result: Optional Twitter sentiment result.
            indicators: Market indicators.

        Returns:
            Human-readable reasoning string.
        """
        twitter_section = ""
        reddit_section = ""
        news_section = ""

        if twitter_result:
            twitter_section = f"""
            Twitter Analysis:
            (sentiment={twitter_result.sentiment}, confidence={twitter_result.confidence})
            {twitter_result.explanation}
            """

        if reddit_result:
            reddit_section = f"""
            Reddit Analysis:
             (sentiment={reddit_result.sentiment}, confidence={reddit_result.confidence})
             {reddit_result.explanation}
            """

        if news_sentiment:
            news_section = f"""
            News Analysis:
             (sentiment={news_sentiment.sentiment}, confidence={news_sentiment.confidence})
             {news_sentiment.explanation}
            """

        # Supermemory context removed — always empty.
        memory_section = ""

        prompt = self.prompt_manager.get_prompt(
            "final_sentiment",
            memory_section=memory_section,
            weight_dict=weight_dict,
            twitter_section=twitter_section,
            reddit_section=reddit_section,
            news_section=news_section,
            indicators=indicators,
        )

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
                created_utc = post.get("created_utc")
                if created_utc is not None:
                    post_dt = datetime.fromtimestamp(float(created_utc))
                    post_dt = to_ist(post_dt)
                    logger.info(
                        f"Post '{post.get('title', '')}' created at "
                        f"{post_dt.isoformat()} (since={since.isoformat()})"
                    )
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
    # TWITTER NEW-TWEET DETECTION
    # ------------------------------------------------------------------

    def _fetch_new_tweets(
        self, since: Optional[datetime]
    ) -> tuple[List[Dict[str, Any]], int]:
        """
        Fetch tweets and filter to only those created after *since*.

        Args:
            since: Datetime threshold. Tweets at or before this time are
                   dropped. When ``None`` all fetched tweets are treated as new.

        Returns:
            Tuple of (new_tweets, new_tweet_count).
        """
        new_tweets = self.twitter_client.fetch_tweets_since(since=since)

        for tweet in new_tweets:
            created_iso: Optional[str] = tweet.get("createdAtISO")
            if created_iso:
                try:
                    tweet_dt = datetime.fromisoformat(created_iso)
                    if tweet_dt.tzinfo is not None:
                        tweet_dt = tweet_dt.astimezone(ZoneInfo("Asia/Kolkata"))
                    if (
                        self._last_twitter_fetch is None
                        or tweet_dt > self._last_twitter_fetch
                    ):
                        self._last_twitter_fetch = tweet_dt
                except ValueError:
                    logger.exception(f"Failed to parse tweet timestamp: {created_iso}")

        logger.info(f"Twitter: {len(new_tweets)} new tweets since {since}")
        return new_tweets, len(new_tweets)

    # ------------------------------------------------------------------
    # LIGHTWEIGHT NEWS + REDDIT + TWITTER UPDATE
    # ------------------------------------------------------------------

    async def run_news_update(
        self,
        last_news_fetch: Optional[datetime] = None,
        last_reddit_fetch: Optional[datetime] = None,
        last_twitter_fetch: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Lightweight update: check RSS news, Reddit, and Twitter for new
        content and re-score sentiment only when something new is found.

        This is designed to be called every 10 minutes.

        Args:
            last_news_fetch: Timestamp of the last news fetch (from Redis).
            last_reddit_fetch: Timestamp of the last Reddit fetch (from Redis).
            last_twitter_fetch: Timestamp of the last Twitter fetch (from Redis).

        Returns:
            Dict with keys:
                ``has_new_data``, ``has_new_articles``, ``has_new_reddit_posts``,
                ``has_new_tweets``, ``news_sentiment``, ``twitter_sentiment``,
                ``new_article_count``, ``new_reddit_post_count``,
                ``new_tweet_count``, ``last_news_fetch``, ``last_reddit_fetch``,
                ``last_twitter_fetch``, ``timestamp``, ``success``,
        """
        effective_news_since: Optional[datetime] = last_news_fetch or self._last_news_fetch
        effective_reddit_since: Optional[datetime] = last_reddit_fetch or self._last_reddit_fetch
        effective_twitter_since: Optional[datetime] = last_twitter_fetch or self._last_twitter_fetch

        now = get_indian_time()

        logger.info(
            f"run_news_update: last_news_fetch={effective_news_since}, "
            f"last_reddit_fetch={effective_reddit_since}, "
            f"last_twitter_fetch={effective_twitter_since}"
        )

        try:
            # ---- RSS News ----
            all_rss = fetch_all_rss_news()
            all_rss = deduplicate(all_rss)
            all_rss = sort_latest(all_rss)

            if effective_news_since is not None:
                new_articles = [
                    a for a in all_rss
                    if a["published"] is not None and a["published"] > effective_news_since
                ]
            else:
                new_articles = filter_recent(all_rss, hours=1)

            has_new_articles = bool(new_articles)
            new_article_count = len(new_articles)

            if new_articles and new_articles[0]["published"] is not None:
                self._last_news_fetch = new_articles[0]["published"]

            if not has_new_articles:
                logger.info("run_news_update: no new RSS articles found.")

            # ---- Reddit ----
            new_reddit_data, new_reddit_post_count = self._fetch_new_reddit_posts(
                since=effective_reddit_since,
                hours_back=1,
                posts_per_subreddit=15,
            )
            has_new_reddit_posts = new_reddit_post_count > 0

            if not has_new_reddit_posts:
                logger.info("run_news_update: no new Reddit posts found.")

            # ---- Twitter ----
            new_tweets, new_tweet_count = self._fetch_new_tweets(
                since=effective_twitter_since
            )
            has_new_tweets = new_tweet_count > 0

            if not has_new_tweets:
                logger.info("run_news_update: no new tweets found.")

            has_new_data = has_new_articles or has_new_reddit_posts or has_new_tweets

            if not has_new_data:
                logger.info(
                    "run_news_update: nothing new from RSS, Reddit, or Twitter — skipping LLM."
                )
                return {
                    "success": True,
                    "has_new_data": False,
                    "has_new_articles": False,
                    "has_new_reddit_posts": False,
                    "has_new_tweets": False,
                    "news_sentiment": None,
                    "twitter_sentiment": None,
                    "new_article_count": 0,
                    "new_reddit_post_count": 0,
                    "new_tweet_count": 0,
                    "last_news_fetch": self._last_news_fetch.isoformat() if self._last_news_fetch else None,
                    "last_reddit_fetch": self._last_reddit_fetch.isoformat() if self._last_reddit_fetch else None,
                    "last_twitter_fetch": self._last_twitter_fetch.isoformat() if self._last_twitter_fetch else None,
                    "timestamp": now.isoformat(),
                }

            logger.info(
                f"run_news_update: {new_article_count} new RSS articles, "
                f"{new_reddit_post_count} new Reddit posts, "
                f"{new_tweet_count} new tweets — running LLM sentiment."
            )

            # ---- Twitter sentiment (highest priority) ----
            twitter_sentiment: Optional[TwitterSentimentResult] = None
            if has_new_tweets:
                twitter_sentiment = self.analyze_twitter(new_tweets)


            # ---- Reddit sentiment (chunk-then-synthesise) ----
            reddit_sentiment: Optional[RedditOverallResult] = None
            if has_new_reddit_posts:
                reddit_sentiment = self.analyze_reddit(new_reddit_data)

            # ---- Build news text for LLM call ----
            news_sentiment: Optional[NewsSentiment] = None
            if has_new_articles:
                news_text = format_for_llm(new_articles, limit=25)

                news_sentiment = self.get_market_sentiments(news_text=news_text)

            # ---- Blend Twitter + News + Reddit into a single incremental sentiment ----
            blended_sentiment: Optional[Sentiment] = self._blend_incremental_sentiment(
                twitter_result=twitter_sentiment,
                news_result=news_sentiment,
                reddit_result=reddit_sentiment,
            )


            return {
                "success": True,
                "has_new_data": True,
                "has_new_articles": has_new_articles,
                "has_new_reddit_posts": has_new_reddit_posts,
                "has_new_tweets": has_new_tweets,
                "news_sentiment": blended_sentiment,
                "twitter_sentiment": twitter_sentiment,
                "new_article_count": new_article_count,
                "new_reddit_post_count": new_reddit_post_count,
                "new_tweet_count": new_tweet_count,
                "last_news_fetch": self._last_news_fetch.isoformat() if self._last_news_fetch else None,
                "last_reddit_fetch": self._last_reddit_fetch.isoformat() if self._last_reddit_fetch else None,
                "last_twitter_fetch": self._last_twitter_fetch.isoformat() if self._last_twitter_fetch else None,
                "timestamp": now.isoformat(),
            }

        except Exception as e:
            logger.exception("run_news_update failed")
            self.handle_exception(
                exception=e,
                context_description="run_news_update",
            )
            return {
                "success": False,
                "has_new_data": False,
                "has_new_articles": False,
                "has_new_reddit_posts": False,
                "has_new_tweets": False,
                "news_sentiment": None,
                "twitter_sentiment": None,
                "new_article_count": 0,
                "new_reddit_post_count": 0,
                "new_tweet_count": 0,
                "error": str(e),
                "last_news_fetch": self._last_news_fetch.isoformat() if self._last_news_fetch else None,
                "last_reddit_fetch": self._last_reddit_fetch.isoformat() if self._last_reddit_fetch else None,
                "last_twitter_fetch": self._last_twitter_fetch.isoformat() if self._last_twitter_fetch else None,
                "timestamp": now.isoformat(),
            }

    # ------------------------------------------------------------------
    # INCREMENTAL BLEND HELPER
    # ------------------------------------------------------------------

    def _blend_incremental_sentiment(
        self,
        twitter_result: Optional[TwitterSentimentResult],
        news_result: Optional[NewsSentiment],
        reddit_result: Optional[RedditOverallResult],
    ) -> Optional[Sentiment]:
        """
        Blend Twitter, News, and Reddit incremental sentiments into a single
        :class:`NewsSentiment` for the Croner drift-detection path.

        Args:
            twitter_result: Aggregated Twitter sentiment (may be None).
            news_result: LLM news sentiment (may be None).
            reddit_result: Synthesised Reddit sentiment (may be None).

        Returns:
            Blended :class:`NewsSentiment`, or ``None`` when all inputs are None.
        """
        if twitter_result is None and news_result is None and reddit_result is None:
            return None

        twitter_section = ""
        reddit_section = ""
        news_section = ""

        if twitter_result:
            twitter_section = f"""
            Twitter Analysis:
            (sentiment={twitter_result.sentiment}, confidence={twitter_result.confidence})
            {twitter_result.explanation}
            """

        if reddit_result:
            reddit_section = f"""
            Reddit Analysis:
             (sentiment={reddit_result.sentiment}, confidence={reddit_result.confidence})
             {reddit_result.explanation}
            """

        if news_result:
            news_section = f"""
            News Analysis:
             (sentiment={news_result.sentiment}, confidence={news_result.confidence})
             {news_result.explanation}
            """

        prompt = self.prompt_manager.get_prompt(
            "blend_incremental",
            weight_dict=weight_dict,
            twitter_section=twitter_section,
            reddit_section=reddit_section,
            news_section=news_section,
        )

        try:
            content = self.llm.invoke(prompt)
            content = str(content).strip()
            blend_sentiment: Dict[str, Any] = extract_json(content)  # validate JSON format
            # extract_json may return a list; handle it the same way as in get_market_sentiments
            if isinstance(blend_sentiment, list):
                if len(blend_sentiment) > 0 and isinstance(blend_sentiment[0], dict):
                    blend_sentiment = blend_sentiment[0]
                else:
                    raise ValueError("LLM returned a list without a valid dict element")
            return Sentiment(**blend_sentiment)
        except Exception as e:
            self.handle_exception(
                exception=e,
                context_description="Blending incremental sentiment failed",
            )
            return Sentiment(
                sentiment=SentimentType.NEUTRAL,
                confidence=0.0,
                explanation="Blended sentiment analysis failed.",
            )

    # ------------------------------------------------------------------
    # MAIN WORKFLOW
    # ------------------------------------------------------------------

    async def run_analysis(self) -> Dict[str, Any]:
        """
        Execute the complete sentiment analysis pipeline.

        Steps:
        1. Fetch Reddit posts and analyse with chunk-then-synthesise LLM
        2. Fetch financial tweets and analyse with chunk-then-synthesise LLM
        3. Fetch RSS news and macro indicators
        4. Compute combined sentiment score (Twitter weighted highest)
        5. Persist results to MongoDB
        6. Return structured result

        Returns:
            Dictionary containing final sentiment analysis results.
        """
        start_time = time.time()

        try:
            # ---- Reddit (chunk-then-synthesise) ----
            reddit_posts_data = self.fetch_reddit()
            reddit_result: RedditOverallResult = self.analyze_reddit(reddit_posts_data)

            # ---- Twitter (chunk-then-synthesise) ----
            tweets = self.fetch_twitter()
            twitter_result: TwitterSentimentResult = self.analyze_twitter(tweets)


            # ---- News + Indicators ----
            news_text = self.fetch_news(hours_back=4, limit=30)
            indicators = self.fetch_indicators()

            news_sentiment = self.get_market_sentiments(news_text=news_text)

            historical_sentiment: List[Dict[str, Any]] = self.mongodb.get_recent_sentiments(hours=24)

            label_weights = {
                "BULLISH": 1,
                "NEUTRAL": 0,
                "BEARISH": -1,
            }

            historical_score: float = (
                sum(label_weights.get(s.get("combined_sentiment", {}).get("sentiment"), 0) for s in historical_sentiment)
                / len(historical_sentiment)
                if historical_sentiment
                else 0
            )

            combined_result: Sentiment = self._combine_results(
                reddit_result,
                news_sentiment,
                twitter_result=twitter_result,
                indicators=indicators,
                historical_score=historical_score,
            )


            record_id = self._save_to_database(
                reddit_result,
                news_text,
                indicators,
                combined_result,
                twitter_result,
                int((time.time() - start_time) * 1000),
            )

            now = get_indian_time()
            self._last_news_fetch = now
            self._last_reddit_fetch = now

            # trend = self.mongodb.calculate_trends(hours=24)
            # signal = self.mongodb.get_trading_signals()

            return {
                "success": True,
                "timestamp": get_indian_time().isoformat(),
                "database_id": record_id,
                **combined_result.model_dump(),
                "reddit_sentiment": {
                    **reddit_result.model_dump()
                },
                "twitter_sentiment": {
                    **twitter_result.model_dump()
                },
                # "trends": trend.dict() if trend else None,
                # "trading_signal": signal,
                "processing_time_ms": int(
                    (time.time() - start_time) * 1000
                ),
            }

        except Exception as e:
            self.handle_exception(
                exception=e,
                context_description="run_analysis",
            )
            return {
                "success": False,
                "error": str(e),
                "timestamp": get_indian_time().isoformat(),
            }

    async def run_web_search_analysis(self) -> Dict[str, Any]:
        """Run the half-hourly live-web market assessment and persist its result."""
        start_time = time.time()
        try:
            current_time = datetime.now(timezone.utc).isoformat()
            prompt = self.prompt_manager.get_prompt(
                "global_crypto_web_sentiment_v3", current_time_utc=current_time
            )
            raw_content = self.llm.invoke_web_search(prompt)
            data = extract_json(str(raw_content))
            result = WebSearchSentiment(**data)
            processing_time = int((time.time() - start_time) * 1000)
            record = SentimentRecord(
                combined_sentiment={
                    "sentiment": result.sentiment,
                    "confidence": result.confidence,
                    "explanation": result.explanation,
                },
                reddit_sentiment={"source": "disabled_for_web_search"},
                news_sentiment={
                    "source": "live_web_search",
                    "sources": result.sources,
                    "summary": result.explanation,
                },
                market_indicators={},
                twitter_sentiment={"source": "disabled_for_web_search"},
                processing_time_ms=processing_time,
            )
            record_id = self.mongodb.save_sentiment(record)
            return {
                "success": True,
                "timestamp": get_indian_time().isoformat(),
                "database_id": record_id,
                "source": "live_web_search",
                **result.model_dump(),
                "processing_time_ms": processing_time,
            }
        except Exception as error:
            self.handle_exception(error, context_description="run_web_search_analysis")
            return {
                "success": False,
                "error": str(error),
                "timestamp": get_indian_time().isoformat(),
                "source": "live_web_search",
            }

    # ------------------------------------------------------------------
    # INTERNAL HELPERS
    # ------------------------------------------------------------------

    def _combine_results(
        self,
        reddit_result: RedditOverallResult,
        news_sentiment: NewsSentiment,
        twitter_result: TwitterSentimentResult,
        indicators: MarketIndicators,
        historical_score: float = 0.0,
    ) -> Sentiment:
        """
        Combine Twitter, Reddit, News, Indicator signals and historical score
        into a final sentiment score.

        Weight priority:
            Twitter (0.35) > News (0.30) > Reddit (0.20) >
            Historical (0.10) > VIX (0.025) + Fear&Greed (0.025)

        Args:
            reddit_result: Synthesised Reddit sentiment result.
            news_sentiment: LLM news sentiment result.
            twitter_result: Aggregated Twitter sentiment.
            indicators: Macro market indicators.
            historical_score: Average score from the last 24 h of DB records.

        Returns:
            Dictionary with label, confidence and explanation.
        """

        reasoning: str = self.get_reasoning(reddit_result, news_sentiment, twitter_result, indicators)
        combined_data: Dict[str, Any] = extract_json(reasoning)
        # extract_json may return a list; handle it the same way as in get_market_sentiments
        if isinstance(combined_data, list):
            if len(combined_data) > 0 and isinstance(combined_data[0], dict):
                combined_data = combined_data[0]
            else:
                raise ValueError("LLM returned a list without a valid dict element")
        return Sentiment(**combined_data)

    def _save_to_database(
        self,
        reddit_result: RedditOverallResult,
        news_text: str,
        indicators: MarketIndicators,
        combined: Sentiment,
        twitter_result: TwitterSentimentResult,
        processing_time: int,
    ) -> str:
        """
        Persist sentiment analysis record to MongoDB.

        Args:
            reddit_result: Synthesised Reddit sentiment result.
            news_text: Raw news text (truncated for storage).
            indicators: Macro market indicators.
            combined: Combined sentiment result dict.
            processing_time: Processing time in milliseconds.
            twitter_result: Optional Twitter sentiment result.

        Returns:
            Inserted record ID.
        """

        record = SentimentRecord(
            combined_sentiment=combined.model_dump(),
            reddit_sentiment=reddit_result.model_dump(),
            news_sentiment={
                "summary": news_text[:500] if news_text else "",
                "source": "rss_feeds",
            },
            market_indicators={
                "vix": indicators.vix,
                "fear_greed_index": indicators.fear_greed_index,
            },
            twitter_sentiment=twitter_result.model_dump(),
            processing_time_ms=processing_time,
        )

        return self.mongodb.save_sentiment(record)
