# config/reddit_config.py
from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

class SubredditCategory(str, Enum):
    TRADING = "trading"           # Trading-focused subs
    INVESTING = "investing"       # Long-term investing
    CRYPTO = "crypto"             # Cryptocurrency specific
    ECONOMICS = "economics"        # Macro economics
    MARKETS = "markets"            # General market discussion
    SATELLITE = "satellite"        # Related but less direct

class WeightedSubreddit(BaseModel):
    name: str
    category: SubredditCategory
    base_weight: float = Field(ge=0.1, le=1.0)
    activity_multiplier: float = Field(default=1.0)
    credibility_score: float = Field(default=0.8, ge=0, le=1)
    min_posts_per_analysis: int = Field(default=5)
    
    def calculate_dynamic_weight(self, posts_count: int, avg_score: float) -> float:
        """Calculate dynamic weight based on activity and engagement"""
        # Activity factor (more posts = more influence)
        activity_factor = min(1.5, 1.0 + (posts_count / 100))
        
        # Engagement factor (highly upvoted content = more influence)
        engagement_factor = min(1.3, 1.0 + (avg_score / 500))

        calculated_weight = self.base_weight * activity_factor * engagement_factor * self.credibility_score 
        calculated_weight = self.base_weight * self.credibility_score # Simplified for Avoiding random dynamic scaling
        
        return calculated_weight

# Configuration for weighted subreddits
WEIGHTED_SUBREDDITS = {
    # Trading focused (Highest weight for crypto trading)
    "wallstreetbets": WeightedSubreddit(
        name="wallstreetbets",
        category=SubredditCategory.TRADING,
        base_weight=0.9,
        credibility_score=0.7  # Lower due to memes/satire
    ),
    "cryptomarkets": WeightedSubreddit(
        name="cryptomarkets",
        category=SubredditCategory.TRADING,
        base_weight=0.95,
        credibility_score=0.85
    ),
    "cryptotrading": WeightedSubreddit(
        name="cryptotrading",
        category=SubredditCategory.TRADING,
        base_weight=0.95,
        credibility_score=0.9
    ),
    
    # Crypto specific
    "bitcoin": WeightedSubreddit(
        name="bitcoin",
        category=SubredditCategory.CRYPTO,
        base_weight=0.9,
        credibility_score=0.8
    ),
    "ethereum": WeightedSubreddit(
        name="ethereum",
        category=SubredditCategory.CRYPTO,
        base_weight=0.85,
        credibility_score=0.8
    ),
    "cryptocurrency": WeightedSubreddit(
        name="cryptocurrency",
        category=SubredditCategory.CRYPTO,
        base_weight=0.85,
        credibility_score=0.75
    ),
    "defi": WeightedSubreddit(
        name="defi",
        category=SubredditCategory.CRYPTO,
        base_weight=0.8,
        credibility_score=0.85
    ),
    
    # Investing (Long-term perspective)
    "investing": WeightedSubreddit(
        name="investing",
        category=SubredditCategory.INVESTING,
        base_weight=0.7,
        credibility_score=0.85
    ),
    "stocks": WeightedSubreddit(
        name="stocks",
        category=SubredditCategory.INVESTING,
        base_weight=0.65,
        credibility_score=0.8
    ),
    "dividends": WeightedSubreddit(
        name="dividends",
        category=SubredditCategory.INVESTING,
        base_weight=0.5,
        credibility_score=0.75
    ),
    
    # Economics (Macro context)
    "economics": WeightedSubreddit(
        name="economics",
        category=SubredditCategory.ECONOMICS,
        base_weight=0.6,
        credibility_score=0.9
    ),
    "economy": WeightedSubreddit(
        name="economy",
        category=SubredditCategory.ECONOMICS,
        base_weight=0.55,
        credibility_score=0.85
    ),
    
    # General markets
    "stockmarket": WeightedSubreddit(
        name="stockmarket",
        category=SubredditCategory.MARKETS,
        base_weight=0.6,
        credibility_score=0.8
    ),
    "finance": WeightedSubreddit(
        name="finance",
        category=SubredditCategory.MARKETS,
        base_weight=0.55,
        credibility_score=0.8
    ),
    
    # Satellite subs (lower weight but useful context)
    "technology": WeightedSubreddit(
        name="technology",
        category=SubredditCategory.SATELLITE,
        base_weight=0.3,
        credibility_score=0.7
    ),
    "business": WeightedSubreddit(
        name="business",
        category=SubredditCategory.SATELLITE,
        base_weight=0.35,
        credibility_score=0.75
    ),
}

# Category-based sentiment impact for crypto trading
CATEGORY_SENTIMENT_IMPACT = {
    SubredditCategory.TRADING: 0.35,      # 35% impact on final sentiment
    SubredditCategory.CRYPTO: 0.30,        # 30% impact
    SubredditCategory.INVESTING: 0.15,      # 15% impact
    SubredditCategory.ECONOMICS: 0.10,      # 10% impact
    SubredditCategory.MARKETS: 0.07,        # 7% impact
    SubredditCategory.SATELLITE: 0.03,      # 3% impact
}