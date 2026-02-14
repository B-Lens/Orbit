

import os
import logging
import time
import requests
import yfinance as yf
import pandas as pd
from functools import lru_cache
from typing import Optional
from pydantic import BaseModel, Field, field_validator
from langchain_core.chat_models import BaseChatModel
from langchain_core.chat_models import ChatGroq
from langchain_core.tools import tool
from src.orbit.utils.utils import require_env
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file

os.environ["GROQ_API_KEY"] = require_env("GROQ_API_KEY")
GROQ_MODEL = "meta-llama/llama-4-maverick-17b-128e-instruct"  # Use a more reliable model

logger = logging.getLogger(__name__)

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

# Initialize LLM with fallback
def initialize_llm()-> Optional[BaseChatModel]:
    """Initialize LLM with fallback options."""
    try:
        llm = ChatGroq(model=GROQ_MODEL, temperature=0, timeout=30)
        # Test the model
        test_response = llm.invoke("Test")
        print(f"test_response: {test_response}")
        logger.info("Groq model initialized successfully")
        return llm
    except Exception as e:
        logger.error(f"Failed to initialize Groq model: {e}")
        # Try alternative models
        alternative_models = ["llama-3.1-8b-instant", "gemma2-9b-it"]
        for alt_model in alternative_models:
            try:
                logger.info(f"Trying alternative model: {alt_model}")
                llm = ChatGroq(model=alt_model, temperature=0, timeout=30)
                test_response = llm.invoke("Test")
                logger.info(f"Alternative model {alt_model} initialized successfully")
                return llm
            except Exception as alt_e:
                logger.error(f"Failed to initialize {alt_model}: {alt_e}")
                continue
        
        # If all Groq models fail, return None
        logger.error("All Groq models failed to initialize")
        return None

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
        logger.error(f"Error fetching VIX: {e}")
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
        logger.error(f"Error fetching Crypto Fear & Greed: {e}")
        return None
    

def fetch_market_indicators() -> MarketIndicators:
    """Fetch real market indicators like VIX and Fear & Greed Index with caching."""
    indicators = MarketIndicators()

    current_hour = time.localtime().tm_hour
    
    try:
        # Fetch VIX index
        indicators.vix = fetch_vix_index(current_hour)
    except Exception as e:
        logger.error(f"Error fetching VIX: {e}")

    try:
        # Fetch Fear & Greed Index
        indicators.fear_greed_index = fetch_crypto_fear_greed(current_hour)
    except Exception as e:
        logger.error(f"Error fetching Fear & Greed Index: {e}")

    return indicators