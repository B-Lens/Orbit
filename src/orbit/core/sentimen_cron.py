import os
import sys
import time
import redis
import asyncio
from orbit.market_intelligence.lang_inference_workflow import inference
from orbit.market_intelligence.sentimental_workflow import SentimentWorkflow
from orbit.market_intelligence.utils.utils import initialize_llm
from orbit.core.exception_manager import ExceptionManager
from orbit.utils.utils import get_indian_time


import logging
from config.config import load_config

logger = logging.getLogger("Orbit")

class Croner(ExceptionManager):

    def __init__(self, logger=None, isTesting=False):
        super().__init__(logger)
        self.redis_client = redis.StrictRedis(
            host="localhost", port=6379, db=0, decode_responses=True
        )
        llm = initialize_llm()
        self.sentimental_workflow = SentimentWorkflow(llm=llm)

    async def run_once(self):
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
            fields=None,
        )

        self.redis_client.setex("market_sentiments", 3600, sentiment)
        return result

    def sentiment_croner(self):
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
