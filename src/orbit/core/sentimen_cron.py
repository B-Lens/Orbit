"""
sentimen_cron
=============

Provides :class:`Croner`, a scheduler that runs live web-grounded global crypto
sentiment analysis every 30 minutes. All heavy dependencies can be injected.
"""

import time
import asyncio
import logging
from typing import Any, Dict, Optional

import redis

from orbit.market_intelligence.sentimental_workflow import SentimentWorkflow
from orbit.market_intelligence.llm.llm_endpoint import LLM
from orbit.core.exception_manager import ExceptionManager
from orbit.core.redis_manager import RedisManager
from orbit.utils.utils import get_indian_time

logger = logging.getLogger("Orbit")

# Check frequently so each half-hour analysis starts close to its boundary.
SCHEDULER_POLL_INTERVAL_SECONDS: int = 60

# If the new sentiment label differs from the cached Redis label AND
# the confidence of the new sentiment exceeds this threshold, trigger an
# immediate full analysis.
SENTIMENT_DRIFT_THRESHOLD: float = 0.55

# A directional regime is useful until the market repeatedly demonstrates that
# its edge has disappeared. One neutral observation is often just an uneventful
# news window, so require two credible observations before clearing the signal.
NEUTRAL_CONFIDENCE_THRESHOLD: float = 0.60
NEUTRAL_CONFIRMATIONS_REQUIRED: int = 2


class Croner(ExceptionManager, RedisManager):
    """Half-hourly web-search sentiment scheduler.

    Args:
        sentiment_workflow: Pre-built :class:`SentimentWorkflow`.  When
            ``None`` a new workflow is created using a fresh :class:`LLM`.
        redis_client: Pre-built ``redis.StrictRedis`` connection.  A default
            ``localhost:6379/0`` connection is created when ``None``.
        custom_logger: Optional logger forwarded to :class:`ExceptionManager`.
        scheduler_poll_interval: Override the scheduler polling interval.
        sentiment_drift_threshold: Override the confidence threshold that
            triggers an immediate full analysis on sentiment drift.
        neutral_confidence_threshold: Minimum confidence for a neutral
            observation to count toward clearing a directional signal.
        neutral_confirmations_required: Consecutive credible neutral half-hourly
            observations required before the cached signal becomes neutral.
    """

    def __init__(
        self,
        sentiment_workflow: Optional[SentimentWorkflow] = None,
        redis_client: Optional[redis.StrictRedis] = None,
        custom_logger: Optional[logging.Logger] = None,
        scheduler_poll_interval: int = SCHEDULER_POLL_INTERVAL_SECONDS,
        sentiment_drift_threshold: float = SENTIMENT_DRIFT_THRESHOLD,
        neutral_confidence_threshold: float = NEUTRAL_CONFIDENCE_THRESHOLD,
        neutral_confirmations_required: int = NEUTRAL_CONFIRMATIONS_REQUIRED,
    ) -> None:
        ExceptionManager.__init__(self, custom_logger)
        RedisManager.__init__(self, redis_client=redis_client)

        if sentiment_workflow is not None:
            self.sentimental_workflow: SentimentWorkflow = sentiment_workflow
        else:
            llm = LLM()
            self.sentimental_workflow = SentimentWorkflow(llm=llm)

        self.scheduler_poll_interval = scheduler_poll_interval
        self.sentiment_drift_threshold: float = sentiment_drift_threshold
        self.neutral_confidence_threshold = neutral_confidence_threshold
        self.neutral_confirmations_required = neutral_confirmations_required

    # ------------------------------------------------------------------
    # FULL ANALYSIS
    # ------------------------------------------------------------------

    def _resolve_effective_sentiment(
        self, observed: Any, confidence: Any
    ) -> tuple[Optional[str], str, int]:
        """Apply confidence-aware regime hysteresis to a half-hour observation.

        Returns ``(effective_label, action, confirmation_count)``. Directional
        signals can change on one sufficiently confident observation. Clearing a
        directional signal to neutral requires consecutive credible observations,
            preventing a quiet interval from erasing useful market intelligence.
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
            logger.error("Web-search sentiment failed: %s", result.get("error"))
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

    def news_croner(self) -> None:
        """Run one full analysis in each UTC/IST half-hour slot forever."""
        while True:
            try:
                now = get_indian_time()
                current_slot = int(now.strftime("%Y%m%d%H")) * 2 + now.minute // 30
                if self.claim_sentiment_run_slot(current_slot):
                    asyncio.run(self.run_once())

                time.sleep(self.scheduler_poll_interval)
            except Exception as e:
                self.handle_exception(
                    e, context_description="Exception in News Croner"
                )
                time.sleep(self.scheduler_poll_interval)
