"""
sentimen_cron
=============

Provides :class:`Croner`, a simple scheduler that runs the
:class:`SentimentWorkflow` on two cadences:

1. **Full analysis** — once per hour (top of the hour).
2. **News + Reddit + Twitter update** — every ``NEWS_POLL_INTERVAL_SECONDS``
   seconds (default 10 minutes).

Last-fetch timestamps are persisted in Redis so they survive process
restarts.  All heavy dependencies (LLM, workflow, Redis) can be **injected**
through the constructor.

Supermemory drift suppression
------------------------------
Before accepting a sentiment drift as genuine the Croner checks the
*dominant* sentiment stored in Supermemory (the confidence-weighted majority
of the last N sentiment results).  A drift is suppressed when:

- The new signal contradicts the dominant memory sentiment **and**
- The new signal's confidence is below ``MEMORY_OVERRIDE_THRESHOLD``
  (default 0.75).

This prevents a single noisy news article or tweet batch from flipping the
cached sentiment when the broader recent history disagrees.
"""

import time
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

import redis

from orbit.market_intelligence.sentimental_workflow import SentimentWorkflow, NewsSentiment
from orbit.market_intelligence.llm.llm_endpoint import LLM
from orbit.core.exception_manager import ExceptionManager
from orbit.core.redis_manager import RedisManager
from orbit.utils.utils import get_indian_time

logger = logging.getLogger("Orbit")

# How often the lightweight news+reddit+twitter poller runs (seconds)
NEWS_POLL_INTERVAL_SECONDS: int = 600  # 10 minutes

# If the new sentiment label differs from the cached Redis label AND
# the confidence of the new sentiment exceeds this threshold, trigger an
# immediate full analysis.
SENTIMENT_DRIFT_THRESHOLD: float = 0.55

# How long a drift window lasts before the counter is reset (seconds)
DRIFT_WINDOW_SECONDS: int = 86_400  # 24 hours

# Minimum confidence required to override the dominant Supermemory sentiment.
# When a new signal contradicts memory but its confidence is below this value
# the drift is suppressed.
MEMORY_OVERRIDE_THRESHOLD: float = 0.75


class Croner(ExceptionManager, RedisManager):
    """Hourly full-analysis + 10-minute news+Reddit+Twitter update scheduler.

    Args:
        sentiment_workflow: Pre-built :class:`SentimentWorkflow`.  When
            ``None`` a new workflow is created using a fresh :class:`LLM`.
        redis_client: Pre-built ``redis.StrictRedis`` connection.  A default
            ``localhost:6379/0`` connection is created when ``None``.
        custom_logger: Optional logger forwarded to :class:`ExceptionManager`.
        news_poll_interval: Override the news-polling interval in seconds.
        sentiment_drift_threshold: Override the confidence threshold that
            triggers an immediate full analysis on sentiment drift.
        memory_override_threshold: Minimum confidence required for a new
            signal to override the dominant Supermemory sentiment.
    """

    def __init__(
        self,
        sentiment_workflow: Optional[SentimentWorkflow] = None,
        redis_client: Optional[redis.StrictRedis] = None,
        custom_logger: Optional[logging.Logger] = None,
        news_poll_interval: int = NEWS_POLL_INTERVAL_SECONDS,
        sentiment_drift_threshold: float = SENTIMENT_DRIFT_THRESHOLD,
        memory_override_threshold: float = MEMORY_OVERRIDE_THRESHOLD,
    ) -> None:
        ExceptionManager.__init__(self, custom_logger)
        RedisManager.__init__(self, redis_client=redis_client)

        if sentiment_workflow is not None:
            self.sentimental_workflow: SentimentWorkflow = sentiment_workflow
        else:
            llm = LLM()
            self.sentimental_workflow = SentimentWorkflow(llm=llm)

        self.news_poll_interval: int = news_poll_interval
        self.sentiment_drift_threshold: float = sentiment_drift_threshold
        self.memory_override_threshold: float = memory_override_threshold

    # ------------------------------------------------------------------
    # DRIFT WINDOW MANAGEMENT
    # ------------------------------------------------------------------

    def _check_and_reset_drift_window(self) -> None:
        """Check whether the 24-hour drift window has elapsed.

        If it has, send a Discord alert with the final drift count and then
        reset the counter so a fresh window begins.
        """
        window_start: Optional[datetime] = self.get_drift_window_start()
        if window_start is None:
            return

        # Ensure window_start is timezone-aware for comparison
        if window_start.tzinfo is None:
            window_start = window_start.replace(tzinfo=timezone.utc)

        elapsed: timedelta = datetime.now(timezone.utc) - window_start
        if elapsed.total_seconds() < DRIFT_WINDOW_SECONDS:
            return

        drift_count: int = self.get_drift_count()
        window_end_iso: str = datetime.now(timezone.utc).isoformat()

        logger.info(
            f"[DriftWindow] 24-hour window elapsed. "
            f"Total sentiment drifts: {drift_count}. "
            f"Window started: {window_start.isoformat()}, ended: {window_end_iso}. "
            "Resetting drift counter."
        )

        self.send_alerts(
            data=(
                f"Sentiment Drift Window Reset — "
                f"Total drifts in the last 24 hours: {drift_count}"
            ),
            description=(
                f"Window: {window_start.isoformat()} → {window_end_iso}"
            ),
            fields={
                "drift_count": drift_count,
                "window_start": window_start.isoformat(),
                "window_end": window_end_iso,
            },
        )

        self.reset_drift_count()

    def _record_drift(self, from_sentiment: Optional[str], to_sentiment: str) -> None:
        """Increment the drift counter, log the new total, and persist to Redis.

        Args:
            from_sentiment: The previously cached sentiment label (may be ``None``).
            to_sentiment: The newly detected sentiment label.
        """
        new_count: int = self.increment_drift_count()
        logger.info(
            f"[SentimentDrift] Drift #{new_count} detected: "
            f"{from_sentiment!r} → {to_sentiment!r}. "
            f"Total drifts in current 24-hour window: {new_count}."
        )

    # ------------------------------------------------------------------
    # SUPERMEMORY DRIFT SUPPRESSION
    # ------------------------------------------------------------------

    def _is_drift_suppressed_by_memory(
        self,
        new_sentiment: str,
        new_confidence: float,
        dominant_memory_sentiment: Optional[str],
    ) -> bool:
        """Decide whether a detected drift should be suppressed by Supermemory.

        A drift is suppressed when the new signal contradicts the dominant
        memory sentiment **and** the new signal's confidence is below
        :attr:`memory_override_threshold`.  This prevents a single noisy
        article or tweet batch from flipping the cached sentiment when the
        broader recent history disagrees.

        Args:
            new_sentiment: The newly detected sentiment label.
            new_confidence: Confidence of the new sentiment.
            dominant_memory_sentiment: Majority sentiment from Supermemory
                (may be ``None`` when no memories exist yet).

        Returns:
            ``True`` when the drift should be suppressed, ``False`` otherwise.
        """
        if dominant_memory_sentiment is None:
            # No memory yet — do not suppress
            return False

        if new_sentiment == dominant_memory_sentiment:
            # New signal agrees with memory — never suppress
            return False

        if new_confidence >= self.memory_override_threshold:
            # High-confidence signal — allow it to override memory
            logger.info(
                f"[MemoryDriftCheck] New signal ({new_sentiment}, "
                f"conf={new_confidence:.2f}) overrides dominant memory "
                f"({dominant_memory_sentiment}) — confidence above threshold "
                f"({self.memory_override_threshold})."
            )
            return False

        # New signal contradicts memory with insufficient confidence — suppress
        logger.info(
            f"[MemoryDriftCheck] Drift suppressed: new={new_sentiment} "
            f"(conf={new_confidence:.2f}) contradicts dominant memory "
            f"({dominant_memory_sentiment}) and confidence is below "
            f"threshold ({self.memory_override_threshold})."
        )
        return True

    # ------------------------------------------------------------------
    # FULL ANALYSIS
    # ------------------------------------------------------------------

    async def run_once(self) -> Dict[str, Any]:
        """Execute a single full sentiment-analysis cycle.

        Returns:
            The analysis result dict.
        """
        result = await self.sentimental_workflow.run_analysis()
        logger.info(f"Sentiment Analysis Result: {result}")
        sentiment = result.get("sentiment")
        sentiment_confidence = result.get("confidence")
        sentiment_reasoning = result.get("explanation")
        dominant_memory_sentiment = result.get("dominant_memory_sentiment")

        twitter_info = result.get("twitter_sentiment", {})
        twitter_label = twitter_info.get("sentiment", "N/A") if twitter_info else "N/A"
        twitter_conf = twitter_info.get("confidence", "N/A") if twitter_info else "N/A"

        self.send_market_sentiment(
            data=(
                f"Market Sentiment = {sentiment}, Confidence : {sentiment_confidence}, "
                f"Reasoning : {sentiment_reasoning} | "
                f"Memory dominant: {dominant_memory_sentiment}"
            ),
            description=None,
            fields=result,
        )

        try:
            self.set_market_sentiment(sentiment)
        except Exception as e:
            self.handle_exception(
                e,
                context_description="Failed to update Redis after full analysis",
            )

        return result

    # ------------------------------------------------------------------
    # LIGHTWEIGHT NEWS + REDDIT + TWITTER UPDATE
    # ------------------------------------------------------------------

    async def run_news_update_once(self) -> Dict[str, Any]:
        """Execute a single lightweight news + Reddit + Twitter update cycle.

        Supermemory drift suppression is applied before accepting a sentiment
        drift: if the new signal contradicts the dominant memory sentiment and
        its confidence is below :attr:`memory_override_threshold` the drift is
        logged but the cached Redis sentiment is **not** updated.

        Returns:
            The result dict from :meth:`SentimentWorkflow.run_news_update`.
        """
        last_news_fetch, last_reddit_fetch, last_twitter_fetch = (
            self.get_last_fetch_times()
        )

        result = await self.sentimental_workflow.run_news_update(
            last_news_fetch=last_news_fetch,
            last_reddit_fetch=last_reddit_fetch,
            last_twitter_fetch=last_twitter_fetch,
        )

        self.save_last_fetch_times(
            last_news_fetch=result.get("last_news_fetch"),
            last_reddit_fetch=result.get("last_reddit_fetch"),
            last_twitter_fetch=result.get("last_twitter_fetch"),
        )

        if not result.get("success"):
            logger.warning(f"News update failed: {result.get('error')}")
            return result

        if not result.get("has_new_data"):
            logger.info(
                "News update: no new data from news, Reddit, or Twitter — skipping LLM call."
            )
            return result

        news_sentiment: Optional[NewsSentiment] = result.get("news_sentiment")
        new_article_count: int = result.get("new_article_count", 0)
        new_reddit_post_count: int = result.get("new_reddit_post_count", 0)
        new_tweet_count: int = result.get("new_tweet_count", 0)
        dominant_memory_sentiment: Optional[str] = result.get("dominant_memory_sentiment")

        twitter_sentiment = result.get("twitter_sentiment")
        twitter_label = twitter_sentiment.sentiment if twitter_sentiment else "N/A"
        twitter_conf = twitter_sentiment.confidence if twitter_sentiment else "N/A"

        logger.info(
            f"News update: {new_article_count} new articles, "
            f"{new_reddit_post_count} new Reddit posts, "
            f"{new_tweet_count} new tweets, "
            f"blended sentiment={news_sentiment.sentiment if news_sentiment else 'N/A'}, "
            f"twitter={twitter_label}, "
            f"dominant_memory={dominant_memory_sentiment}"
        )

        if news_sentiment:
            cached_sentiment: Optional[str] = self.get_market_sentiment()

            # --- Basic drift check (same as before) ---
            basic_drift: bool = (
                cached_sentiment is None or (
                    news_sentiment.sentiment != cached_sentiment
                    and news_sentiment.confidence >= self.sentiment_drift_threshold
                    and news_sentiment.sentiment in {"BULLISH", "BEARISH"}
                )
            )

            # --- Supermemory drift suppression ---
            memory_suppressed: bool = False
            if basic_drift and cached_sentiment is not None:
                memory_suppressed = self._is_drift_suppressed_by_memory(
                    new_sentiment=news_sentiment.sentiment,
                    new_confidence=news_sentiment.confidence,
                    dominant_memory_sentiment=dominant_memory_sentiment,
                )

            sentiment_drifted: bool = basic_drift and not memory_suppressed

            self.send_market_sentiment(
                data=(
                    f"[Incremental Update] {new_article_count} new articles, "
                    f"{new_reddit_post_count} new Reddit posts, "
                    f"{new_tweet_count} new tweets — "
                    f"Blended Sentiment={news_sentiment.sentiment}, "
                    f"Confidence={news_sentiment.confidence:.2f} | "
                    f"Twitter={twitter_label} (conf={twitter_conf}) | "
                    f"Memory dominant={dominant_memory_sentiment} | "
                    f"Memory suppressed={memory_suppressed}"
                ),
                description=(
                    f"Sentiment drift detected: {sentiment_drifted} "
                    f"(memory_suppressed={memory_suppressed}) "
                    f"::== {cached_sentiment} → {news_sentiment.sentiment}"
                ),
                fields={
                    "sentiment": news_sentiment.sentiment,
                    "confidence": news_sentiment.confidence,
                    "explanation": news_sentiment.explanation,
                    "twitter_sentiment": twitter_label,
                    "twitter_confidence": twitter_conf,
                    "new_articles": new_article_count,
                    "new_reddit_posts": new_reddit_post_count,
                    "new_tweets": new_tweet_count,
                    "dominant_memory_sentiment": dominant_memory_sentiment,
                    "memory_suppressed": memory_suppressed,
                },
            )

            if sentiment_drifted:
                self._record_drift(
                    from_sentiment=cached_sentiment,
                    to_sentiment=news_sentiment.sentiment,
                )
                logger.warning(
                    f"Sentiment drift accepted: cached={cached_sentiment}, "
                    f"new={news_sentiment.sentiment} "
                    f"(confidence={news_sentiment.confidence:.2f} >= "
                    f"{self.sentiment_drift_threshold}). "
                    "Triggering immediate full analysis."
                )
                try:
                    self.set_market_sentiment(news_sentiment.sentiment)
                except Exception as e:
                    logger.exception("Failed to update Redis after incremental analysis.")
                    self.handle_exception(
                        e,
                        context_description="Failed to update Redis after incremental analysis",
                    )
            elif memory_suppressed:
                logger.info(
                    f"Sentiment drift suppressed by Supermemory: "
                    f"cached={cached_sentiment}, "
                    f"new={news_sentiment.sentiment} "
                    f"(conf={news_sentiment.confidence:.2f}), "
                    f"dominant_memory={dominant_memory_sentiment}. "
                    "Redis sentiment NOT updated."
                )

        return result

    def news_croner(self) -> None:
        """Run :meth:`run_news_update_once` every :attr:`news_poll_interval`
        seconds, forever.
        """
        while True:
            try:
                current_hour = get_indian_time().hour

                last_run = self.get_hourly_last_run()

                if last_run != current_hour:
                    asyncio.run(self.run_once())
                    self.set_hourly_last_run(current_hour)
                    time.sleep(self.news_poll_interval)

                # Check whether the 24-hour drift window has elapsed and
                # reset the counter (sending a Discord alert) if so.
                self._check_and_reset_drift_window()

                asyncio.run(self.run_news_update_once())
                time.sleep(self.news_poll_interval)
            except Exception as e:
                self.handle_exception(
                    e, context_description="Exception in News Croner"
                )
                time.sleep(self.news_poll_interval)
