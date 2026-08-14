# models/mongodb_models.py
import os
import pandas as pd
from pymongo import MongoClient, ASCENDING, DESCENDING
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from bson import ObjectId
import logging
from orbit.utils.utils import get_symbol_price

logger = logging.getLogger("Orbit")

RETURN_WINDOWS = {
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}

SYMBOLS = ["BTCUSDT", "ETHUSDT"]

class PyObjectId(ObjectId):
    @classmethod
    def __get_validators__(cls):
        yield cls.validate
    
    @classmethod
    def validate(cls, v):
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid objectid")
        return ObjectId(v)

class SentimentRecord(BaseModel):
    """Main sentiment record for MongoDB"""
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    timestamp: datetime = Field(default_factory=datetime.now)
    
    # Overall sentiment
    combined_sentiment: Dict[str, Any]  # Store the full combined sentiment result
    
    # Reddit Analysis
    reddit_sentiment: Dict[str, Any]
    
    # Legacy Sources
    news_sentiment: Dict[str, Any]
    market_indicators: Dict[str, Any]
    twitter_sentiment: Dict[str, Any] 
    
    # Market Context
    prices: Dict[str, float] = Field(default_factory=dict)

    # Market Outcome (filled later)
    returns: Dict[str, Dict[str, Optional[float]]] = Field(default_factory=dict)


    
    # Metadata
    version: str = "2.0"
    processing_time_ms: int
    
    class Config:
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}

class SentimentTrend(BaseModel):
    """Model for trend analysis"""
    period: str  # 1h, 6h, 24h, 7d
    start_time: datetime
    end_time: datetime
    avg_sentiment: float
    sentiment_volatility: float
    bullish_percentage: float
    bearish_percentage: float
    neutral_percentage: float
    dominant_category: str
    sample_size: int

# MongoDB Manager Class
class MongoDBManager:
    def __init__(self, connection_string: str = None, database: str = 'crypto_sentiment'):
        if connection_string is None:
            connection_string = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
        
        self.client = MongoClient(connection_string)
        self.db = self.client[database]
        
        # Collections
        self.sentiment_history = self.db.sentiment_history
        self.trends = self.db.trends
        
        # Create indexes
        self._create_indexes()
        logger.info(f"Connected to MongoDB: {database}")
    
    def _create_indexes(self):
        """Create necessary indexes for performance"""
        
        # Sentiment history indexes
        self.sentiment_history.create_index([("timestamp", DESCENDING)])
        self.sentiment_history.create_index([("sentiment_label", ASCENDING)])
        self.sentiment_history.create_index([
            ("timestamp", DESCENDING),
            ("sentiment_label", ASCENDING)
        ])
        
        # Compound index for time-based queries
        self.sentiment_history.create_index([
            ("timestamp", DESCENDING),
            ("overall_score", ASCENDING)
        ])
        
        # Index for category queries
        self.sentiment_history.create_index([
            ("reddit_category_breakdown.trading.avg_sentiment", ASCENDING)
        ])
        
        # TTL index for automatic cleanup (optional - keep 30 days)
        self.sentiment_history.create_index(
            "timestamp", 
            expireAfterSeconds=2592000  # 30 days
        )
        
        logger.info("MongoDB indexes created")
    
    def save_sentiment(self, record: SentimentRecord) -> str:
        """Save sentiment record to database"""
        try:

            prices = {}
            for symbol in SYMBOLS:
                price = get_symbol_price(symbol)
                if price:
                    prices[symbol] = price

            record.prices = prices
            result = self.sentiment_history.insert_one(record.dict(by_alias=True))
            logger.info(f"Saved sentiment record with ID: {result.inserted_id}")

            self._update_returns(record.timestamp, prices)
            return str(result.inserted_id)
        except Exception as e:
            logger.error(f"Failed to save sentiment record: {e}")
            raise

    def _update_returns(self, current_time: datetime, current_prices: Dict[str, float]):

        for window_name, delta in RETURN_WINDOWS.items():

            target_time = current_time - delta

            past_record = self.sentiment_history.find_one(
                {
                    "timestamp": {
                        "$gte": target_time - timedelta(minutes=2),
                        "$lte": target_time + timedelta(minutes=2)
                    }
                }
            )

            if not past_record:
                continue

            past_prices = past_record.get("prices", {})
            updates = {}

            for symbol, current_price in current_prices.items():

                past_price = past_prices.get(symbol)
                if not past_price:
                    continue

                ret = (current_price - past_price) / past_price * 100

                updates[f"returns.{symbol}.{window_name}"] = ret

            if updates:
                self.sentiment_history.update_one(
                    {"_id": past_record["_id"]},
                    {"$set": updates}
                )
    
    def get_recent_sentiments(
        self, 
        hours: int = 24, 
        limit: int = 100
    ) -> List[Dict]:
        """Get recent sentiment records"""
        cutoff = datetime.now() - timedelta(hours=hours)
        
        cursor = self.sentiment_history.find(
            {"timestamp": {"$gte": cutoff}}
        ).sort("timestamp", DESCENDING).limit(limit)
        
        return list(cursor)
    
    def get_sentiment_by_date_range(
        self, 
        start_date: datetime, 
        end_date: datetime
    ) -> List[Dict]:
        """Get sentiment records within date range"""
        cursor = self.sentiment_history.find({
            "timestamp": {
                "$gte": start_date,
                "$lte": end_date
            }
        }).sort("timestamp", ASCENDING)
        
        return list(cursor)
    
    def calculate_trends(self, hours: int = 24) -> SentimentTrend:
        """Calculate sentiment trends for a given period"""
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=hours)
        
        # Get records in period
        records = self.get_sentiment_by_date_range(start_time, end_time)
        
        if not records:
            return None
        
        # Calculate metrics
        scores = [r['overall_score'] for r in records]
        labels = [r['sentiment_label'] for r in records]
        
        # Category dominance
        categories = {}
        for r in records:
            for cat in r.get('reddit_category_breakdown', {}):
                categories[cat] = categories.get(cat, 0) + 1
        
        dominant_category = max(categories, key=categories.get) if categories else "unknown"
        
        trend = SentimentTrend(
            period=f"{hours}h",
            start_time=start_time,
            end_time=end_time,
            avg_sentiment=sum(scores) / len(scores),
            sentiment_volatility=pd.Series(scores).std() if len(scores) > 1 else 0,
            bullish_percentage=labels.count('BULLISH') / len(labels) * 100,
            bearish_percentage=labels.count('BEARISH') / len(labels) * 100,
            neutral_percentage=labels.count('NEUTRAL') / len(labels) * 100,
            dominant_category=dominant_category,
            sample_size=len(records)
        )
        
        # Save trend
        self.trends.insert_one(trend.dict())
        
        return trend
    
    def get_trading_signals(self, threshold: float = 0.6) -> Dict:
        """Generate simple trading signals based on sentiment"""
        recent = self.get_recent_sentiments(hours=6)
        
        if len(recent) < 3:
            return {"signal": "INSUFFICIENT_DATA", "confidence": 0}
        
        # Calculate moving average
        scores = [r['overall_score'] for r in recent]
        current = scores[0]
        ma_3 = sum(scores[:3]) / 3
        ma_6 = sum(scores) / len(scores)
        
        # Determine signal
        if current > 0.5 and ma_3 > 0.4 and ma_6 > 0.3:
            signal = "STRONG_BUY"
            confidence = min(0.9, current)
        elif current > 0.3 and ma_3 > 0.2:
            signal = "BUY"
            confidence = 0.6
        elif current < -0.5 and ma_3 < -0.4 and ma_6 < -0.3:
            signal = "STRONG_SELL"
            confidence = min(0.9, abs(current))
        elif current < -0.3 and ma_3 < -0.2:
            signal = "SELL"
            confidence = 0.6
        else:
            signal = "NEUTRAL"
            confidence = 0.5
        
        return {
            "signal": signal,
            "confidence": round(confidence, 2),
            "current_sentiment": round(current, 3),
            "ma_3": round(ma_3, 3),
            "ma_6": round(ma_6, 3),
            "sample_size": len(recent)
        }