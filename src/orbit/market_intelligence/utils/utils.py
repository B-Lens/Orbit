

import os
import logging
import time
import requests
import yfinance as yf
import pandas as pd
from functools import lru_cache
from typing import Optional
from enum import Enum
from pydantic import BaseModel, Field, field_validator
from orbit.utils.utils import require_env
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file

os.environ["GROQ_API_KEY"] = require_env("GROQ_API_KEY")
GROQ_MODEL = "openai/gpt-oss-120b"

logger = logging.getLogger("Orbit")

class MarketIndicators(BaseModel):
    """Model for market indicators."""
    vix: Optional[float] = Field(default=None, description="VIX volatility index value")
    fear_greed_index: Optional[int] = Field(default=None, ge=0, le=100, description="Crypto Fear & Greed Index (0-100)")
    put_call_ratio: Optional[float] = Field(default=None, description="Put/Call ratio (placeholder)")
    
    class Config:
        schema_extra = {
            "example": {
                "vix": 18.5,
                "fear_greed_index": 65,
                "put_call_ratio": 0.85
            }
        }

# Pydantic Models for better type safety and validation
class SentimentType(str, Enum):
    """Enumeration for sentiment types."""
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"

class SentimentResult(BaseModel):
    """Model for parsed sentiment results."""
    sentiment: SentimentType = Field(..., description="The sentiment classification")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score between 0 and 1")
    explanation: str = Field(default="", description="Explanation for the sentiment")
    
    @field_validator('confidence')
    @classmethod
    def validate_confidence(cls, v: float) -> float:
        """Ensure confidence is between 0 and 1."""
        return max(0.0, min(1.0, v))


@lru_cache(maxsize=10)
def fetch_vix_index(time_bucket: int) -> Optional[float]:
    """Fetch and cache the VIX index."""
    try:
        vix_data = yf.Ticker("^VIX").history(period="1d")
        if isinstance(vix_data, pd.DataFrame) and not vix_data.empty:
            vix = vix_data['Close'].iloc[-1]
            if isinstance(vix, (int, float)):  # Ensure the value is numeric
                vix = round(float(vix), 2)
                logger.info(f"VIX fetched: {vix}")
                return vix
    except Exception as e:
        logger.exception(f"Error fetching VIX: {e}")
    return None

@lru_cache(maxsize=10)
def fetch_crypto_fear_greed(time_bucket: int) -> Optional[int]:
    """Fetch and cache the Crypto Fear & Greed Index."""
    try:
        response = requests.get("https://api.alternative.me/fng/", timeout=10)
        response.raise_for_status()
        data = response.json()
        index_value = int(data['data'][0]['value'])
        logger.info(f"Fear & Greed Index fetched: {index_value}")
        return index_value
    except Exception as e:
        logger.exception(f"Error fetching Crypto Fear & Greed: {e}")
        return None
    

def fetch_market_indicators() -> MarketIndicators:
    """Fetch real market indicators like VIX and Fear & Greed Index with caching."""
    indicators = MarketIndicators()

    current_hour = time.localtime().tm_hour
    
    try:
        # Fetch VIX index
        indicators.vix = fetch_vix_index(current_hour)
    except Exception as e:
        logger.exception(f"Error fetching VIX: {e}")

    try:
        # Fetch Fear & Greed Index
        indicators.fear_greed_index = fetch_crypto_fear_greed(current_hour)
    except Exception as e:
        logger.exception(f"Error fetching Fear & Greed Index: {e}")

    return indicators


def parse_sentiment(sentiment: str) -> SentimentResult:
    """Parse sentiment string and extract sentiment, confidence, and explanation."""
    logger.debug(f"Parsing sentiment: {sentiment}")
    
    if not isinstance(sentiment, str) or "Sentiment:" not in sentiment:
        return SentimentResult(sentiment=SentimentType.NEUTRAL, confidence=0.0, explanation="")
    
    try:
        # More robust parsing that handles different formats
        sentiment_val = SentimentType.NEUTRAL
        confidence = 0.0
        explanation = ""
        
        # Extract sentiment
        if "Sentiment:" in sentiment:
            sentiment_part = sentiment.split("Sentiment:")[1].split(",")[0].strip()
            try:
                sentiment_val = SentimentType(sentiment_part.upper())
            except ValueError:
                logger.error(f"Unknown sentiment value: {sentiment_part}")
                sentiment_val = SentimentType.NEUTRAL
        
        # Extract confidence
        if "Confidence:" in sentiment:
            confidence_part = sentiment.split("Confidence:")[1].split(",")[0].strip()
            try:
                confidence = float(confidence_part)
                # Ensure confidence is between 0 and 1
                confidence = max(0.0, min(1.0, confidence))
            except (ValueError, TypeError):
                logger.error(f"Invalid confidence value: {confidence_part}")
                confidence = 0.0
        
        # Extract explanation
        if "Explanation:" in sentiment:
            explanation_part = sentiment.split("Explanation:")[1].strip()
            explanation = explanation_part
        
        return SentimentResult(sentiment=sentiment_val, confidence=confidence, explanation=explanation)
    except Exception as e:
        logger.error(f"Error parsing sentiment: {e}")
        return SentimentResult(sentiment=SentimentType.NEUTRAL, confidence=0.0, explanation="")