"""Shared market-intelligence data types."""

from enum import Enum


class SentimentType(str, Enum):
    """Supported market-sentiment labels."""

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
