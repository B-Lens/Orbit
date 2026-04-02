"""
sentimen_cron
=============

Provides :class:`Croner`, a simple scheduler that runs the
:class:`SentimentWorkflow` once per hour and caches the result in Redis.

All heavy dependencies (LLM, workflow, Redis) can be **injected** through
the constructor.
"""

import time
import asyncio
import logging
from typing import Any, Dict, Optional

import redis

from orbit.market_intelligence.sentimental_workflow import SentimentWorkflow
from orbit.market_intelligence.llm.llm_endpoint import LLM
from orbit.core.exception_manager import ExceptionManager
from orbit.utils.utils import get_indian_time

logger = logging.getLogger("Orbit")


class Croner(ExceptionManager):
    """Hourly sentiment-analysis scheduler.

    Args:
        sentiment_workflow: Pre-built :class:`SentimentWorkflow`.  When
            ``None`` a new workflow is created using a fresh :class:`LLM`.
        redis_client: Pre-built ``redis.StrictRedis`` connection.  A default
            ``localhost:6379/0`` connection is created when ``None``.
        custom_logger: Optional logger forwarded to :class:`ExceptionManager`.
    """

    def __init__(
        self,
        sentiment_workflow: Optional[SentimentWorkflow] = None,
        redis_client: Optional[redis.StrictRedis] = None,
        custom_logger: Optional[logging.Logger] = None,
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

    async def run_once(self) -> Dict[str, Any]:
        """Execute a single sentiment-analysis cycle.

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
                self.handle_exception(e, context_description='Exception in Sentiment Croner')
                time.sleep(90)