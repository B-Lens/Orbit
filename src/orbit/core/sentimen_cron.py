"""
sentimen_cron
=============

Provides :class:`Croner`, a scheduler that runs live web-grounded sentiment
hourly. The legacy source poller can be enabled explicitly:

1. **ChatGPT web-search analysis** — once per hour.
2. **Legacy News + Reddit + Twitter update** — disabled by default.

Last-fetch timestamps are persisted in Redis so they survive process
restarts.  All heavy dependencies (LLM, workflow, Redis) can be **injected**
through the constructor.
"""

import time
import asyncio
import logging
import os
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

# A directional regime is useful until the market repeatedly demonstrates that
# its edge has disappeared. One neutral observation is often just an uneventful
# news window, so require two credible observations before clearing the signal.
NEUTRAL_CONFIDENCE_THRESHOLD: float = 0.60
NEUTRAL_CONFIRMATIONS_REQUIRED: int = 2

# How long a drift window lasts before the counter is reset (seconds)
DRIFT_WINDOW_SECONDS: int = 86_400  # 24 hours



class Croner(ExceptionManager, RedisManager):
    """Hourly web-search sentiment scheduler with optional legacy updates.

    Args:
        sentiment_workflow: Pre-built :class:`SentimentWorkflow`.  When
            ``None`` a new workflow is created using a fresh :class:`LLM`.
        redis_client: Pre-built ``redis.StrictRedis`` connection.  A default
            ``localhost:6379/0`` connection is created when ``None``.
        custom_logger: Optional logger forwarded to :class:`ExceptionManager`.
        news_poll_interval: Override the news-polling interval in seconds.
        sentiment_drift_threshold: Override the confidence threshold that
            triggers an immediate full analysis on sentiment drift.
        neutral_confidence_threshold: Minimum confidence for a neutral
            observation to count toward clearing a directional signal.
        neutral_confirmations_required: Consecutive credible neutral hourly
            observations required before the cached signal becomes neutral.
        legacy_updates_enabled: Enable the legacy 10-minute source poller.
    """

    def __init__(
        self,
        sentiment_workflow: Optional[SentimentWorkflow] = None,
        redis_client: Optional[redis.StrictRedis] = None,
        custom_logger: Optional[logging.Logger] = None,
        news_poll_interval: int = NEWS_POLL_INTERVAL_SECONDS,
        sentiment_drift_threshold: float = SENTIMENT_DRIFT_THRESHOLD,
        neutral_confidence_threshold: float = NEUTRAL_CONFIDENCE_THRESHOLD,
        neutral_confirmations_required: int = NEUTRAL_CONFIRMATIONS_REQUIRED,
        legacy_updates_enabled: Optional[bool] = None,
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
        self.neutral_confidence_threshold = neutral_confidence_threshold
        self.neutral_confirmations_required = neutral_confirmations_required
        self.legacy_updates_enabled = (
            legacy_updates_enabled
            if legacy_updates_enabled is not None
            else os.getenv("ORBIT_LEGACY_SENTIMENT_UPDATES", "false").lower()
            in {"1", "true", "yes"}
        )

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
    # FULL ANALYSIS
    # ------------------------------------------------------------------

    def _resolve_effective_sentiment(
        self, observed: Any, confidence: Any
    ) -> tuple[Optional[str], str, int]:
        """Apply confidence-aware regime hysteresis to an hourly observation.

        Returns ``(effective_label, action, confirmation_count)``. Directional
        signals can change on one sufficiently confident observation. Clearing a
        directional signal to neutral requires consecutive credible observations,
        preventing a quiet hour from erasing useful market intelligence.
        """
        labels = {"BULLISH", "BEARISH", "NEUTRAL"}
        if observed not in labels:
            return self.get_market_sentiment(), "invalid_observation", 0

        try:
            score = float(confidence)
        except (TypeError, ValueError):
            score = 0.0

        cached = self.get_market_sentiment()
        if cached not in labels:
            cached = None

        if cached is None:
            if self.set_market_sentiment_if_current(None, observed):
                return observed, "initialized", 1
            return self.get_market_sentiment(), "regime_changed_during_resolution", 0

        if observed == cached:
            self.clear_pending_sentiment()
            return cached, "confirmed", 1

        if observed == "NEUTRAL":
            if score < self.neutral_confidence_threshold:
                self.clear_pending_sentiment()
                return cached, "neutral_rejected_low_confidence", 0
            confirmation = self.record_pending_sentiment(
                observed, cached, self.neutral_confirmations_required
            )
            if confirmation is None:
                return (
                    self.get_market_sentiment(),
                    "regime_changed_during_resolution",
                    0,
                )
            confirmations, was_committed = confirmation
            if not was_committed:
                return cached, "neutral_pending_confirmation", confirmations
            return observed, "neutral_confirmed", confirmations

        if score < self.sentiment_drift_threshold:
            self.clear_pending_sentiment()
            return cached, "directional_rejected_low_confidence", 0

        if self.set_market_sentiment_if_current(cached, observed):
            return observed, "directional_change", 1
        return self.get_market_sentiment(), "regime_changed_during_resolution", 0

    async def run_once(self) -> Dict[str, Any]:
        """Execute a single full sentiment-analysis cycle.

        Returns:
            The analysis result dict.
        """
        # Work on our own envelope: test doubles and workflow callers may reuse
        # the returned mapping, while the scheduler adds effective-signal metadata.
        result = dict(await self.sentimental_workflow.run_web_search_analysis())
        logger.info(f"Sentiment Analysis Result: {result}")
        if not result.get("success"):
            logger.error("Hourly web-search sentiment failed: %s", result.get("error"))
            return result
        sentiment = result.get("sentiment")
        sentiment_confidence = result.get("confidence")
        sentiment_reasoning = result.get("explanation")
        dominant_memory_sentiment = result.get("dominant_memory_sentiment")

        effective_sentiment, signal_action, confirmation_count = (
            self._resolve_effective_sentiment(sentiment, sentiment_confidence)
        )
        result["observed_sentiment"] = sentiment
        result["effective_sentiment"] = effective_sentiment
        result["signal_action"] = signal_action
        result["confirmation_count"] = confirmation_count

        self.send_market_sentiment(
            data=(
                f"Observed Sentiment = {sentiment}, Effective Signal = {effective_sentiment}, "
                f"Confidence: {sentiment_confidence}, Action: {signal_action}, "
                f"Reasoning : {sentiment_reasoning} | "
                f"Memory dominant: {dominant_memory_sentiment}"
            ),
            description=None,
            fields=result,
        )

        return result

    # ------------------------------------------------------------------
    # LIGHTWEIGHT NEWS + REDDIT + TWITTER UPDATE
    # ------------------------------------------------------------------

    async def run_news_update_once(self) -> Dict[str, Any]:
        """Execute a single lightweight news + Reddit + Twitter update cycle.

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
            sentiment_drifted: bool = (
                cached_sentiment is None or (
                    news_sentiment.sentiment != cached_sentiment
                    and news_sentiment.confidence >= self.sentiment_drift_threshold
                    and news_sentiment.sentiment in {"BULLISH", "BEARISH"}
                )
            )


            self.send_market_sentiment(
                data=(
                    f"[Incremental Update] {new_article_count} new articles, "
                    f"{new_reddit_post_count} new Reddit posts, "
                    f"{new_tweet_count} new tweets — "
                    f"Blended Sentiment={news_sentiment.sentiment}, "
                    f"Confidence={news_sentiment.confidence:.2f} | "
                    f"Twitter={twitter_label} (conf={twitter_conf})"
                ),
                description=(
                    f"Sentiment drift detected: {sentiment_drifted} "
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
                    self.set_market_sentiment_and_clear_pending(
                        news_sentiment.sentiment
                    )
                except Exception as e:
                    logger.exception("Failed to update Redis after incremental analysis.")
                    self.handle_exception(
                        e,
                        context_description="Failed to update Redis after incremental analysis",
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
                    result = asyncio.run(self.run_once())
                    if result.get("success"):
                        self.set_hourly_last_run(current_hour)

                # Check whether the 24-hour drift window has elapsed and
                # reset the counter (sending a Discord alert) if so.
                self._check_and_reset_drift_window()

                if self.legacy_updates_enabled:
                    asyncio.run(self.run_news_update_once())
                time.sleep(self.news_poll_interval)
            except Exception as e:
                self.handle_exception(
                    e, context_description="Exception in News Croner"
                )
                time.sleep(self.news_poll_interval)
