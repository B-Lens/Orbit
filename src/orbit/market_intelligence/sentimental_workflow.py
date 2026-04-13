"""
Sentiment Workflow Module

This module orchestrates the full market sentiment analysis pipeline by:
- Fetching Reddit posts
- Applying weighted Reddit sentiment analysis
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

import os
import time
import logging
from pydantic import BaseModel, Field
from tqdm import tqdm
from datetime import datetime, timezone
from typing import Dict, Any, List, Literal, Optional
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
    RedditSentimentEntry,
    WeightedRedditAnalyzer,
    extract_json,
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
from orbit.market_intelligence.utils.utils import (
    fetch_market_indicators,
    SentimentType,
    MarketIndicators,
)
from orbit.utils.utils import require_env, get_indian_time, to_ist


BATCH_SIZE = 100
logger = logging.getLogger("Orbit")

# ---------------------------------------------------------------------------
# Sentiment combination weights
# ---------------------------------------------------------------------------
_W_TWITTER: float = 0.35
_W_NEWS: float = 0.30
_W_REDDIT: float = 0.20
_W_HISTORICAL: float = 0.10
_W_VIX: float = 0.025
_W_FEAR_GREED: float = 0.025


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


class SentimentWorkflow(ExceptionManager):
    """
    End-to-end market sentiment analysis workflow.

    Responsibilities:
    - Fetch and analyze Reddit sentiment
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

        All tweets are split into chunks; each chunk gets an overall sentiment
        summary from the LLM.  The summaries are then synthesised into a single
        :class:`TwitterSentimentResult` by a final LLM call.

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

    def get_market_sentiments(self, prompt: str) -> NewsSentiment:
        """
        Use LLM to classify overall market sentiment from news articles.

        Args:
            news_text: Combined news article text.
            prompt: Optional custom prompt override.

        Returns:
            Structured NewsSentiment object.
        """

        RETURN_FORMAT = """\n
            Respond ONLY in valid JSON.

            Rules:
            - sentiment MUST be exactly one of: "BULLISH", "BEARISH", "NEUTRAL"
            - Do NOT return multiple values
            - Do NOT include "|" symbol
            - Do NOT explain inside sentiment

            Respond in Json Format:
            {{
                "sentiment": "BULLISH",
                "confidence": 0.0,
                "explanation": "brief explanation"
            }}
            """

        full_prompt = prompt + "\n" + RETURN_FORMAT

        try:
            raw_content = self.llm.invoke(full_prompt)
            raw_content = str(raw_content)
            logger.info(f"LLM raw output for news sentiment: {raw_content}")
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
                sentiment=SentimentType.NEUTRAL,
                confidence=0.3,
                explanation="Analysis failed",
            )
            self.last_news_sentiment = fallback
            return fallback

    def get_reasoning(
        self,
        posts: List[Dict[str, Any]],
        news_sentiment: NewsSentiment,
        twitter_result: Optional[TwitterSentimentResult] = None,
    ) -> str:
        """
        Generate LLM-based reasoning for final market sentiment.

        Args:
            posts: Top influential Reddit posts.
            news_sentiment: Structured news sentiment result.
            twitter_result: Optional Twitter sentiment result.

        Returns:
            Human-readable reasoning string.
        """
        posts_summary = "\n".join(p["explanation"] for p in posts)

        twitter_section = ""
        if twitter_result:
            twitter_section = f"""
        Twitter/X Sentiment:
        {twitter_result.explanation}
        (score={twitter_result.overall_score}, confidence={twitter_result.confidence})
        """

        prompt = f"""
        Provide reasoning for overall market sentiment:
        {twitter_section}
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
                ``last_twitter_fetch``, ``timestamp``, ``success``.
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

            # ---- Build combined text for news+reddit LLM call ----
            combined_text_parts: List[str] = []

            if has_new_articles:
                news_text = format_for_llm(new_articles, limit=25)
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
                        "[REDDIT]\n" + "\n\n".join(reddit_snippets)
                    )

            news_sentiment: Optional[NewsSentiment] = None
            if combined_text_parts:
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

                    News and Reddit posts:
                    {combined_text}
                """

                news_sentiment = self.get_market_sentiments(prompt=prompt)

            # ---- Blend Twitter + News/Reddit into a single incremental sentiment ----
            blended_sentiment: Optional[NewsSentiment] = self._blend_incremental_sentiment(
                twitter_result=twitter_sentiment,
                news_result=news_sentiment,
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
    ) -> Optional[NewsSentiment]:
        """
        Blend Twitter and News/Reddit incremental sentiments into a single
        :class:`NewsSentiment` for the Croner drift-detection path.

        Args:
            twitter_result: Aggregated Twitter sentiment (may be None).
            news_result: LLM news+reddit sentiment (may be None).

        Returns:
            Blended :class:`NewsSentiment`, or ``None`` when both inputs are None.
        """
        if twitter_result is None and news_result is None:
            return None

        direction_map = {"BULLISH": 1.0, "BEARISH": -1.0, "NEUTRAL": 0.0}

        total_w = 0.0
        weighted_score = 0.0
        weighted_conf = 0.0
        explanations: List[str] = []

        if twitter_result is not None:
            w = _W_TWITTER
            d = direction_map.get(twitter_result.sentiment, 0.0)
            weighted_score += d * twitter_result.confidence * w
            weighted_conf += twitter_result.confidence * w
            total_w += w
            explanations.append(
                f"Twitter({twitter_result.sentiment}, conf={twitter_result.confidence:.2f})"
            )

        if news_result is not None:
            w = _W_NEWS
            d = direction_map.get(news_result.sentiment, 0.0)
            weighted_score += d * news_result.confidence * w
            weighted_conf += news_result.confidence * w
            total_w += w
            explanations.append(
                f"News({news_result.sentiment}, conf={news_result.confidence:.2f})"
            )

        if total_w == 0:
            return None

        score = weighted_score / total_w
        conf = round(min(1.0, weighted_conf / total_w), 3)

        if score > 0.1:
            label = SentimentType.BULLISH
        elif score < -0.1:
            label = SentimentType.BEARISH
        else:
            label = SentimentType.NEUTRAL

        return NewsSentiment(
            sentiment=label,
            confidence=conf,
            explanation=" | ".join(explanations),
        )

    # ------------------------------------------------------------------
    # MAIN WORKFLOW
    # ------------------------------------------------------------------

    async def run_analysis(self) -> Dict[str, Any]:
        """
        Execute the complete sentiment analysis pipeline.

        Steps:
        1. Fetch Reddit posts and aggregate Reddit sentiment
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
            # ---- Reddit ----
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

            # ---- Twitter (chunk-then-synthesise) ----
            tweets = self.fetch_twitter()
            twitter_result: TwitterSentimentResult = self.analyze_twitter(tweets)

            # ---- News + Indicators ----
            news_text = self.fetch_news(hours_back=8, limit=30)
            indicators = self.fetch_indicators()

            PROMPT = f"""
                Analyze overall market sentiment from the following news.

                Consider:
                - macroeconomic impact
                - gold reaction
                - crypto reaction
                - risk-on / risk-off tone
                - central bank signals

                News:
                {news_text}
            """
            news_sentiment = self.get_market_sentiments(prompt=PROMPT)

            historical_sentiment: List[Dict[str, Any]] = self.mongodb.get_recent_sentiments(hours=24)
            historical_score: float = (
                sum(s["overall_score"] for s in historical_sentiment) / len(historical_sentiment)
                if historical_sentiment
                else 0
            )

            reasoning: str = self.get_reasoning(top_posts, news_sentiment, twitter_result)

            combined_result = self._combine_results(
                reddit_result,
                news_sentiment,
                indicators,
                twitter_result=twitter_result,
                historical_score=historical_score,
            )

            record_id = self._save_to_database(
                reddit_result,
                top_posts,
                news_text,
                indicators,
                combined_result,
                int((time.time() - start_time) * 1000),
                twitter_result=twitter_result,
            )

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
                "twitter_sentiment": {
                    "sentiment": twitter_result.sentiment,
                    "confidence": twitter_result.confidence,
                    "overall_score": twitter_result.overall_score,
                    "total_tweets_analyzed": twitter_result.total_tweets_analyzed,
                    "explanation": twitter_result.explanation,
                },
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
                context_description="run_analysis",
            )
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
        twitter_result: Optional[TwitterSentimentResult] = None,
        historical_score: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Combine Twitter, Reddit, News, Indicator signals and historical score
        into a final sentiment score.

        Weight priority:
            Twitter (0.35) > News (0.30) > Reddit (0.20) >
            Historical (0.10) > VIX (0.025) + Fear&Greed (0.025)

        Args:
            reddit_result: Aggregated Reddit sentiment dict.
            news_sentiment: LLM news sentiment result.
            indicators: Macro market indicators.
            twitter_result: Aggregated Twitter sentiment (optional).
            historical_score: Average score from the last 24 h of DB records.

        Returns:
            Dictionary with combined score, label, and confidence.
        """
        direction_map = {
            SentimentType.BULLISH: 1.0,
            SentimentType.BEARISH: -1.0,
        }

        # ---- Twitter component ----
        twitter_component = 0.0
        twitter_conf_contribution = 0.0
        if twitter_result is not None:
            t_dir = (
                1.0 if twitter_result.sentiment == "BULLISH"
                else -1.0 if twitter_result.sentiment == "BEARISH"
                else 0.0
            )
            twitter_component = twitter_result.confidence * _W_TWITTER * t_dir
            twitter_conf_contribution = twitter_result.confidence * _W_TWITTER

        # ---- News component ----
        news_dir = direction_map.get(news_sentiment.sentiment, 0.0)
        news_component = news_sentiment.confidence * _W_NEWS * news_dir
        news_conf_contribution = news_sentiment.confidence * _W_NEWS

        # ---- Reddit component ----
        reddit_component = reddit_result["overall_score"] * _W_REDDIT

        # ---- Historical component ----
        historical_component = historical_score * _W_HISTORICAL

        # ---- Indicator components ----
        fear_greed = indicators.fear_greed_index
        fear_greed_component: float = 0.0
        if fear_greed is not None:
            direction = 1 if fear_greed < 50 else -1
            fear_greed_component = fear_greed * _W_FEAR_GREED * direction

        vix_component = (indicators.vix or 0) * _W_VIX * -1

        combined_score = (
            twitter_component
            + news_component
            + reddit_component
            + historical_component
            + vix_component
            + fear_greed_component
        )

        if combined_score > 0.2:
            label = "BULLISH"
        elif combined_score < -0.2:
            label = "BEARISH"
        else:
            label = "NEUTRAL"

        confidence = round(
            twitter_conf_contribution + news_conf_contribution,
            2,
        )

        return {
            "score": round(combined_score, 3),
            "sentiment": label,
            "confidence": min(1.0, confidence),
        }

    def _save_to_database(
        self,
        reddit_result: Dict[str, Any],
        top_posts: List[Dict[str, Any]],
        news_text: str,
        indicators: MarketIndicators,
        combined: Dict[str, Any],
        processing_time: int,
        twitter_result: Optional[TwitterSentimentResult] = None,
    ) -> str:
        """
        Persist sentiment analysis record to MongoDB.

        Args:
            reddit_result: Aggregated Reddit sentiment dict.
            top_posts: Top influential Reddit posts.
            news_text: Raw news text (truncated for storage).
            indicators: Macro market indicators.
            combined: Combined sentiment result dict.
            processing_time: Processing time in milliseconds.
            twitter_result: Optional Twitter sentiment result.

        Returns:
            Inserted record ID.
        """
        twitter_data: Dict[str, Any] = {}
        if twitter_result is not None:
            twitter_data = {
                "sentiment": twitter_result.sentiment,
                "confidence": twitter_result.confidence,
                "overall_score": twitter_result.overall_score,
                "total_tweets_analyzed": twitter_result.total_tweets_analyzed,
                "explanation": twitter_result.explanation,
            }

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
                "source": "rss_feeds",
            },
            market_indicators={
                "vix": indicators.vix,
                "fear_greed_index": indicators.fear_greed_index,
            },
            twitter_sentiment=twitter_data,
            processing_time_ms=processing_time,
        )

        return self.mongodb.save_sentiment(record)
