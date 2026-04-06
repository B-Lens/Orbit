"""
sentimen_cron
=============

Provides :class:`Croner`, a simple scheduler that runs the
:class:`SentimentWorkflow` on two cadences:

1. **Full analysis** — once per hour (top of the hour).  Runs the complete
   Reddit + news + indicator pipeline and caches the result in Redis.

2. **News + Reddit update** — every ``NEWS_POLL_INTERVAL_SECONDS`` seconds
   (default 10 minutes).  Fetches only *new* articles since the last call
   AND checks Reddit for new posts since the last call.  Re-scores sentiment
   with the LLM only when something new is found, keeping token usage low.
   A full analysis is also triggered immediately when the new sentiment
   diverges from the cached value by more than ``SENTIMENT_DRIFT_THRESHOLD``.

Last-fetch timestamps are persisted in Redis so they survive process
restarts.  All heavy dependencies (LLM, workflow, Redis) can be **injected**
through the constructor.
"""

import time
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import redis

from orbit.market_intelligence.sentimental_workflow import SentimentWorkflow, NewsSentiment
from orbit.market_intelligence.llm.llm_endpoint import LLM
from orbit.core.exception_manager import ExceptionManager
from orbit.utils.utils import get_indian_time

logger = logging.getLogger("Orbit")

# How often the lightweight news+reddit poller runs (seconds)
NEWS_POLL_INTERVAL_SECONDS: int = 600  # 10 minutes

# If the new sentiment label differs from the cached Redis label AND
# the confidence of the new sentiment exceeds this threshold, trigger an
# immediate full analysis to avoid acting on low-confidence signals.
SENTIMENT_DRIFT_THRESHOLD: float = 0.55

# Redis keys
_REDIS_KEY_LAST_NEWS_FETCH: str = "sentiment:last_news_fetch"
_REDIS_KEY_LAST_REDDIT_FETCH: str = "sentiment:last_reddit_fetch"
_REDIS_KEY_MARKET_SENTIMENT: str = "market_sentiments"


class Croner(ExceptionManager):
    """Hourly full-analysis + 10-minute news+Reddit update scheduler.

    Args:
        sentiment_workflow: Pre-built :class:`SentimentWorkflow`.  When
            ``None`` a new workflow is created using a fresh :class:`LLM`.
        redis_client: Pre-built ``redis.StrictRedis`` connection.  A default
            ``localhost:6379/0`` connection is created when ``None``.
        custom_logger: Optional logger forwarded to :class:`ExceptionManager`.
        news_poll_interval: Override the news-polling interval in seconds.
        sentiment_drift_threshold: Override the confidence threshold that
            triggers an immediate full analysis on sentiment drift.
    """

    def __init__(
        self,
        sentiment_workflow: Optional[SentimentWorkflow] = None,
        redis_client: Optional[redis.StrictRedis] = None,
        custom_logger: Optional[logging.Logger] = None,
        news_poll_interval: int = NEWS_POLL_INTERVAL_SECONDS,
        sentiment_drift_threshold: float = SENTIMENT_DRIFT_THRESHOLD,
    ) -> None:
        super().__init__(custom_logger)

        self.redis_client: redis.StrictRedis = redis_client or redis.StrictRedis(
            host="localhost", port=6379, db=0, decode_responses=True
        )

        if sentiment_workflow is not None:
            self.sentimental_workflow: SentimentWorkflow = sentiment_workflow
        else:
            llm = LLM()
            self.sentimental_workflow = SentimentWorkflow(llm=llm)

        self.news_poll_interval: int = news_poll_interval
        self.sentiment_drift_threshold: float = sentiment_drift_threshold

    # ------------------------------------------------------------------
    # REDIS TIMESTAMP HELPERS
    # ------------------------------------------------------------------

    def _load_last_fetch_times(self) -> tuple[Optional[datetime], Optional[datetime]]:
        """
        Load last-fetch timestamps from Redis.

        Returns:
            Tuple of (last_news_fetch, last_reddit_fetch).
            Either value is ``None`` when not yet stored in Redis.
        """
        last_news_fetch: Optional[datetime] = None
        last_reddit_fetch: Optional[datetime] = None

        try:
            raw_news = self.redis_client.get(_REDIS_KEY_LAST_NEWS_FETCH)
            if raw_news:
                last_news_fetch = datetime.fromisoformat(raw_news)

            raw_reddit = self.redis_client.get(_REDIS_KEY_LAST_REDDIT_FETCH)
            if raw_reddit:
                last_reddit_fetch = datetime.fromisoformat(raw_reddit)
        except Exception:
            logger.exception("Failed to load last-fetch timestamps from Redis.")

        return last_news_fetch, last_reddit_fetch

    def _save_last_fetch_times(
        self,
        last_news_fetch: Optional[str],
        last_reddit_fetch: Optional[str],
    ) -> None:
        """
        Persist last-fetch timestamps to Redis.

        Args:
            last_news_fetch: ISO-8601 string for the last news fetch time.
            last_reddit_fetch: ISO-8601 string for the last Reddit fetch time.
        """
        try:
            if last_news_fetch:
                # Keep for 48 hours — well beyond any polling window
                self.redis_client.setex(
                    _REDIS_KEY_LAST_NEWS_FETCH, 172800, last_news_fetch
                )
            if last_reddit_fetch:
                self.redis_client.setex(
                    _REDIS_KEY_LAST_REDDIT_FETCH, 172800, last_reddit_fetch
                )
        except Exception:
            logger.exception("Failed to save last-fetch timestamps to Redis.")

    # ------------------------------------------------------------------
    # FULL ANALYSIS
    # ------------------------------------------------------------------

    async def run_once(self) -> Dict[str, Any]:
        """Execute a single full sentiment-analysis cycle.

        After a successful run the last-fetch timestamps in Redis are updated
        so the lightweight poller does not re-process the same data.

        Returns:
            The analysis result dict (keys typically include ``sentiment``,
            ``confidence``, ``reasoning``).
        """
        result = await self.sentimental_workflow.run_analysis()
        logger.info(f"Sentiment Analysis Result: {result}")
        sentiment = result.get("sentiment")
        sentiment_confidence = result.get("confidence")
        sentiment_reasoning = result.get("reasoning")
        self.send_market_sentiment(
            data=(
                f"Market Sentiment = {sentiment}, Confidence : {sentiment_confidence}, "
                f"Reasoning : {sentiment_reasoning}"
            ),
            description=None,
            fields=result,
        )

        # Cache sentiment label and update fetch timestamps
        try:
            self.redis_client.set(_REDIS_KEY_MARKET_SENTIMENT, sentiment)
        except Exception as e:
            logger.exception("Failed to update Redis after full analysis.")
            self.handle_exception(
                e,
                context_description="Failed to update Redis after full analysis",
            )

        return result

    # ------------------------------------------------------------------
    # LIGHTWEIGHT NEWS + REDDIT UPDATE
    # ------------------------------------------------------------------

    async def run_news_update_once(self) -> Dict[str, Any]:
        """
        Execute a single lightweight news + Reddit update cycle.

        Steps:
        1. Load last-fetch timestamps from Redis (survives restarts).
        2. Pass them to :meth:`SentimentWorkflow.run_news_update`.
        3. Persist the updated timestamps back to Redis.

        Returns:
            The result dict from :meth:`SentimentWorkflow.run_news_update`.
        """
        # Load persisted timestamps so we don't re-process old data after restart
        last_news_fetch, last_reddit_fetch = self._load_last_fetch_times()

        result = await self.sentimental_workflow.run_news_update(
            last_news_fetch=last_news_fetch,
            last_reddit_fetch=last_reddit_fetch,
        )

        # Persist updated timestamps regardless of success so the next call
        # always has the most recent window.
        self._save_last_fetch_times(
            last_news_fetch=result.get("last_news_fetch"),
            last_reddit_fetch=result.get("last_reddit_fetch"),
        )

        if not result.get("success"):
            logger.warning(f"News update failed: {result.get('error')}")
            return result

        if not result.get("has_new_data"):
            logger.info("News update: no new data from news or Reddit — skipping LLM call.")
            return result

        news_sentiment: Optional[NewsSentiment] = result.get("news_sentiment")
        new_article_count: int = result.get("new_article_count", 0)
        new_reddit_post_count: int = result.get("new_reddit_post_count", 0)

        logger.info(
            f"News update: {new_article_count} new articles, "
            f"{new_reddit_post_count} new Reddit posts, "
            f"sentiment={news_sentiment.sentiment if news_sentiment else 'N/A'}, "
            f"confidence={news_sentiment.confidence if news_sentiment else 'N/A'}"
        )

        if news_sentiment: # news_sentiment is combined sentiment

            # Check for sentiment drift vs cached value
            cached_sentiment: Optional[str] = self.redis_client.get(
                _REDIS_KEY_MARKET_SENTIMENT
            )
            sentiment_drifted: bool = (
                cached_sentiment is None or (
                    news_sentiment.sentiment != cached_sentiment
                    and news_sentiment.confidence >= self.sentiment_drift_threshold
                    and news_sentiment.sentiment in {"BULLISH", "BEARISH"}  # Only trigger on clear directional shifts
                )
            )

            # Notify Discord about the incremental update
            self.send_market_sentiment(
                data=(
                    f"[Incremental Update] {new_article_count} new articles, "
                    f"{new_reddit_post_count} new Reddit posts — "
                    f"Sentiment={news_sentiment.sentiment}, "
                    f"Confidence={news_sentiment.confidence:.2f}"
                ),
                description=f"Sentiment drift detected: {sentiment_drifted} ::== {cached_sentiment} → {news_sentiment.sentiment}",
                fields={
                    "sentiment": news_sentiment.sentiment,
                    "confidence": news_sentiment.confidence,
                    "explanation": news_sentiment.explanation,
                    "new_articles": new_article_count,
                    "new_reddit_posts": new_reddit_post_count,
                },
            )


            if sentiment_drifted:
                logger.warning(
                    f"Sentiment drift detected: cached={cached_sentiment}, "
                    f"new={news_sentiment.sentiment} "
                    f"(confidence={news_sentiment.confidence:.2f} >= "
                    f"{self.sentiment_drift_threshold}). "
                    "Triggering immediate full analysis."
                )
                # Cache sentiment label and update fetch timestamps
                try:
                    self.redis_client.set(_REDIS_KEY_MARKET_SENTIMENT, news_sentiment.sentiment)
                except Exception as e:
                    logger.exception("Failed to update Redis after incremental analysis.")
                    self.handle_exception(
                        e,
                        context_description="Failed to update Redis after incremental analysis",
                    )

        return result

    def news_croner(self) -> None:
        """
        Run :meth:`run_news_update_once` every :attr:`news_poll_interval`
        seconds, forever.

        Design notes
        ------------
        * Runs hourly full-analysis loop and incremental news updates independently.
        * Loads and saves last-fetch timestamps from/to Redis on every cycle
          so the correct deduplication window is maintained across restarts.
        * Only calls the LLM when new news articles or Reddit posts are found,
          keeping token usage low.
        """
        while True:
            try:
                current_hour = get_indian_time().hour

                last_run = self.redis_client.get("ms_hourly_last_run")
                last_run = int(last_run) if last_run else None

                if last_run != current_hour:
                    asyncio.run(self.run_once())

                    self.redis_client.set(
                        "ms_hourly_last_run",
                        current_hour
                    )
                    time.sleep(self.news_poll_interval)
                asyncio.run(self.run_news_update_once())
                time.sleep(self.news_poll_interval)
            except Exception as e:
                self.handle_exception(
                    e, context_description="Exception in News Croner"
                )
                time.sleep(self.news_poll_interval)
