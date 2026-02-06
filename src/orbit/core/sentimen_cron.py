import os
import sys
import time
import redis
from orbit.ai.lang_inference import inference
from orbit.core.exception_manager import ExceptionManager
from orbit.utils.utils import get_indian_time, process_memory


import logging
from config.config import *

logger = logging.getLogger("Orbit")

class Croner(ExceptionManager):

    def __init__(self, logger=None, isTesting=False):
        super().__init__(logger)
        self.isTesting = isTesting
        self.redis_client = redis.StrictRedis(
            host="localhost", port=6379, db=0, decode_responses=True
        )

    def sentiment_croner(self):
        """
        This function runs the sentiment analysis pipeline every hour.
        It fetches news articles and analyzes their sentiments.
        """
        while True:
            try:
                current_time = get_indian_time()
                if current_time.minute == 0 or self.isTesting:
                    sentiment_ret = inference()
                    logger.info(f"Sentiment Analysis Result: {sentiment_ret}")
                    sentiment = sentiment_ret.get("sentiment")
                    sentiment_confidence = sentiment_ret.get("confidence")
                    sentiment_explanation = sentiment_ret.get("explanation")
                    news_sentiment = sentiment_ret.get("news_explanation")
                    social_sentiment = sentiment_ret.get("social_explanation")
                    self.send_market_sentiment(
                        data=(
                            f"Market Sentiment = {sentiment}, Confidence : {sentiment_confidence}, "
                            f"Explanation : {sentiment_explanation}"
                        ),
                        description=f"News Sentiment : {news_sentiment}, \n\n\n Social Sentiment : {social_sentiment}",
                        fields=None,
                    )
                    if self.isTesting:
                        return True
                    self.redis_client.setex("market_sentiments", 3600, sentiment)
                    time.sleep(90)
                time.sleep(30)
            except Exception as e:
                self.handle_exception(e, context_description='Exception in Sentiment Croner')
                if self.isTesting:
                    break 
                time.sleep(30)
        
        return False
