"""
sentimen_cron
=============

Provides :class:`Croner`, a simple scheduler that runs the
:class:`SentimentWorkflow` on two cadences:

1. **Full analysis** — once per hour (top of the hour).  Runs the complete
   Reddit + news + indicator pipeline and caches the result in Redis.

2. **News-only update** — every ``NEWS_POLL_INTERVAL_SECONDS`` seconds
   (default 10 minutes).  Fetches only *new* articles since the last call,
   re-scores news sentiment with the LLM, and patches the latest MongoDB
   record.  A full analysis is also triggered immediately when the new news
   sentiment diverges from the cached value by more than
   ``SENTIMENT_DRIFT_THRESHOLD``.

All heavy dependencies (LLM, workflow, Redis) can be **injected** through
the constructor.
"""

import time
import asyncio
import logging
from typing import Any, Dict, Optional

import redis

from orbit.market_intelligence.sentimental_workflow import SentimentWorkflow, NewsSentiment
from orbit.market_intelligence.llm.llm_endpoint import LLM
from orbit.core.exception_manager import ExceptionManager
from orbit.utils.utils import get_indian_time

logger = logging.getLogger("Orbit")

# How often the lightweight news poller runs (seconds)
NEWS_POLL_INTERVAL_SECONDS: int = 600  # 10 minutes

# If the new news-sentiment label differs from the cached Redis label AND
# the confidence of the new sentiment exceeds this threshold, trigger an
# immediate full analysis to avoid wasting tokens on low-confidence signals.
SENTIMENT_DRIFT_THRESHOLD: float = 0.55


class Croner(ExceptionManager):
    """Hourly full-analysis + 10-minute news-update scheduler.

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
    # FULL ANALYSIS
    # ------------------------------------------------------------------

    async def run_once(self) -> Dict[str, Any]:
        """Execute a single full sentiment-analysis cycle.

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
        self.redis_client.setex("market_sentiments", 3600, sentiment)
        return result

    def sentiment_croner(self) -> None:
        """Run :meth:`run_once` at the top of every hour, forever.

        This blocking method is designed to be executed inside a dedicated
        daemon thread.
        """
        while True:
            try:
                current_time = get_indian_time()
                if current_time.minute == 0:
                    asyncio.run(self.run_once())
                    time.sleep(90)
                time.sleep(30)
            except Exception as e:
                self.handle_exception(
                    e, context_description="Exception in Sentiment Croner"
                )
                time.sleep(90)

    # ------------------------------------------------------------------
    # LIGHTWEIGHT NEWS UPDATE
    # ------------------------------------------------------------------

    async def run_news_update_once(self) -> Dict[str, Any]:
        """
        Execute a single lightweight news-only update cycle.

        If new articles are found and the resulting sentiment diverges from
        the currently cached Redis value (with sufficient confidence), a full
        ``run_once()`` is triggered immediately to refresh all signals.

        Returns:
            The result dict from :meth:`SentimentWorkflow.run_news_update`.
        """
        result = await self.sentimental_workflow.run_news_update()

        if not result.get("success"):
            logger.warning(f"News update failed: {result.get('error')}")
            return result

        if not result.get("has_new_articles"):
            logger.info("News update: no new articles — skipping LLM call.")
            return result

        news_sentiment: Optional[NewsSentiment] = result.get("news_sentiment")
        new_article_count: int = result.get("new_article_count", 0)

        logger.info(
            f"News update: {new_article_count} new articles, "
            f"sentiment={news_sentiment.sentiment if news_sentiment else 'N/A'}, "
            f"confidence={news_sentiment.confidence if news_sentiment else 'N/A'}"
        )

        if news_sentiment:
            # Notify Discord about the incremental news update
            self.send_market_sentiment(
                data=(
                    f"[News Update] {new_article_count} new articles — "
                    f"Sentiment={news_sentiment.sentiment}, "
                    f"Confidence={news_sentiment.confidence:.2f}"
                ),
                description="Incremental news sentiment update",
                fields={
                    "sentiment": news_sentiment.sentiment,
                    "confidence": news_sentiment.confidence,
                    "explanation": news_sentiment.explanation,
                    "new_articles": new_article_count,
                },
            )

            # Check for sentiment drift vs cached value
            cached_sentiment: Optional[str] = self.redis_client.get(
                "market_sentiments"
            )
            sentiment_drifted: bool = (
                cached_sentiment is not None
                and news_sentiment.sentiment != cached_sentiment
                and news_sentiment.confidence >= self.sentiment_drift_threshold
            )

            if sentiment_drifted:
                logger.warning(
                    f"Sentiment drift detected: cached={cached_sentiment}, "
                    f"new={news_sentiment.sentiment} "
                    f"(confidence={news_sentiment.confidence:.2f} >= "
                    f"{self.sentiment_drift_threshold}). "
                    "Triggering immediate full analysis."
                )
                await self.run_once()

        return result

    def news_croner(self) -> None:
        """
        Run :meth:`run_news_update_once` every
        :attr:`news_poll_interval` seconds, forever.

        This blocking method is designed to be executed inside a dedicated
        daemon thread alongside :meth:`sentiment_croner`.

        Design notes
        ------------
        * Runs independently of the hourly full-analysis loop.
        * Only calls the LLM when new articles are actually found, keeping
          token usage low.
        * Triggers a full analysis automatically on significant sentiment
          drift so the Redis cache and MongoDB record stay fresh.
        """
        while True:
            try:
                asyncio.run(self.run_news_update_once())
                time.sleep(self.news_poll_interval)
            except Exception as e:
                self.handle_exception(
                    e, context_description="Exception in News Croner"
                )
                time.sleep(self.news_poll_interval)
