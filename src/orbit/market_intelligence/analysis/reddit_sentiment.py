# analysis/weighted_reddit_sentiment.py
import json
import logging
import re
from typing import List, Dict, Any
import numpy as np
from datetime import datetime
from pydantic import BaseModel, Field
from orbit.market_intelligence.config.reddit_config import CATEGORY_SENTIMENT_IMPACT

logger = logging.getLogger("Orbit")

class RedditSentimentEntry(BaseModel):
    """Model for individual Reddit post sentiment"""
    post_id: str
    subreddit: str
    category: str
    title: str
    text_snippet: str
    score: int
    num_comments: int
    raw_sentiment: str  # bullish/bearish/neutral
    confidence: float
    relevance: float = Field(ge=0, le=1)
    base_weight: float
    dynamic_weight: float
    engagement_multiplier: float
    final_weight: float
    timestamp: float
    explanation: str = ""

class CategoryAggregation(BaseModel):
    """Model for category-wise sentiment aggregation"""
    category: str
    total_weight: float
    weighted_sentiment_sum: float
    avg_confidence: float
    post_count: int
    avg_sentiment: float
    impact_factor: float
    weighted_contribution: float

def extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON found in LLM response")

    return json.loads(match.group(0))

class WeightedRedditAnalyzer:
    def __init__(self, llm) -> None:
        self.llm = llm
        
    async def analyze_post_sentiment(
        self, 
        post_batch: Dict, 
        dynamic_weight: float
    ) -> RedditSentimentEntry:
        """Analyze individual Reddit post with weighting"""
        
        # Prepare content for analysis
        content = f"Title: {post['title']}\n\nContent: {post['text'][:500]}"

        merged = []

        for post in post_batch:
            body = (post.get("body") or "")[:800]

            merged.append(f"""
                Title: {post['title']}
                Body: {body}
                """)

        content = "\n---\n".join(merged)
        
        # Use LLM for sentiment analysis
        prompt = f"""
        Analyze the sentiment of this Reddit post about cryptocurrency/Financial markets:
        
        {content}
        
        Focus on the overall market/crypto sentiment, not individual stocks.
        
        Respond in JSON format:
        {{
            "sentiment": "BULLISH|BEARISH|NEUTRAL",
            "confidence": 0.0-1.0,
            "relevance": 0.0-1.0 (how relevant is this to crypto/market sentiment),
            "explanation": "brief explanation"
        }}
        """
        
        try:
            result = await self.llm.ainvoke(prompt)
            logger.info(f"LLM Result for batch : {result.content}")
            sentiment_data = extract_json(result.content)
            logger.info(f"Extracted Sentiment Data for batch: {sentiment_data}")
        except Exception as e:
            logger.exception(f"LLM analysis failed: {e}, using fallback")
            sentiment_data = {
                "sentiment": "NEUTRAL",
                "confidence": 0.3,
                "relevance": 0.5,
                "explanation": "Analysis failed"
            }
        
        # Calculate engagement multiplier
        engagement_score = post['engagement_score']
        engagement_multiplier = min(2.0, 1.0 + engagement_score)
        
        # Calculate final weight
        final_weight = dynamic_weight * engagement_multiplier * sentiment_data['relevance']
        
        return RedditSentimentEntry(
            post_id=post['id'],
            subreddit=post['subreddit'],
            category=post['category'],
            title=post['title'],
            text_snippet=post['text'][:200],
            score=post['score'],
            num_comments=post['num_comments'],
            raw_sentiment=sentiment_data['sentiment'],
            confidence=sentiment_data['confidence'],
            relevance=sentiment_data['relevance'],
            base_weight=post['base_weight'],
            dynamic_weight=dynamic_weight,
            engagement_multiplier=engagement_multiplier,
            final_weight=final_weight,
            timestamp=post['created_utc'],
            explanation=sentiment_data['explanation']
        )
    
    def aggregate_weighted_sentiment(
        self, 
        sentiments: List[RedditSentimentEntry]
    ) -> Dict[str, Any]:
        """Aggregate weighted sentiments by category"""
        
        category_data = {}
        total_weight = 0
        weighted_sentiment_score = 0
        total_confidence = 0
        
        # Group by category
        for entry in sentiments:
            category = entry.category
            
            if category not in category_data:
                category_data[category] = {
                    'total_weight': 0,
                    'sentiment_sum': 0,
                    'confidence_sum': 0,
                    'count': 0,
                    'entries': []
                }
            
            # Convert sentiment to numeric
            sentiment_num = {
                'BULLISH': 1.0,
                'NEUTRAL': 0.0,
                'BEARISH': -1.0
            }.get(entry.raw_sentiment.upper(), 0.0)
            
            # Apply confidence to sentiment
            adjusted_sentiment = sentiment_num * entry.confidence
            
            cat = category_data[category]
            cat['total_weight'] += entry.final_weight
            cat['sentiment_sum'] += adjusted_sentiment * entry.final_weight
            cat['confidence_sum'] += entry.confidence
            cat['count'] += 1
            cat['entries'].append(entry)
            
            total_weight += entry.final_weight
            weighted_sentiment_score += adjusted_sentiment * entry.final_weight
            total_confidence += entry.confidence
        
        # Normalize overall score
        if total_weight > 0:
            weighted_sentiment_score /= total_weight
            avg_confidence = total_confidence / len(sentiments) if sentiments else 0
        else:
            weighted_sentiment_score = 0
            avg_confidence = 0
        
        # Calculate category-wise impact
        category_breakdown = {}
        for category, data in category_data.items():
            if data['count'] > 0 and data['total_weight'] > 0:
                avg_category_sentiment = data['sentiment_sum'] / data['total_weight']
                impact = CATEGORY_SENTIMENT_IMPACT.get(category, 0.1)
                
                category_breakdown[category] = {
                    'avg_sentiment': round(avg_category_sentiment, 3),
                    'impact_factor': impact,
                    'weighted_contribution': round(avg_category_sentiment * impact, 3),
                    'post_count': data['count'],
                    'total_weight': round(data['total_weight'], 2),
                    'avg_confidence': round(data['confidence_sum'] / data['count'], 2)
                }
        
        # Determine sentiment label
        if weighted_sentiment_score > 0.2:
            sentiment_label = 'BULLISH'
        elif weighted_sentiment_score < -0.2:
            sentiment_label = 'BEARISH'
        else:
            sentiment_label = 'NEUTRAL'
        
        return {
            'overall_score': round(weighted_sentiment_score, 3),
            'sentiment_label': sentiment_label,
            'confidence': round(avg_confidence, 2),
            'category_breakdown': category_breakdown,
            'total_posts_analyzed': len(sentiments),
            'total_weight_applied': round(total_weight, 2),
            'timestamp': datetime.now().isoformat()
        }
    
    def get_top_influential_posts(
        self, 
        sentiments: List[RedditSentimentEntry], 
        limit: int = 10
    ) -> List[Dict]:
        """Get most influential posts based on final weight"""
        sorted_posts = sorted(
            sentiments, 
            key=lambda x: x.final_weight * abs(
                1 if x.raw_sentiment == 'BULLISH' else -1 if x.raw_sentiment == 'BEARISH' else 0
            ),
            reverse=True
        )
        
        influential = []
        for post in sorted_posts[:limit]:
            influential.append({
                'subreddit': f"r/{post.subreddit}",
                'title': post.title[:100] + ('...' if len(post.title) > 100 else ''),
                'sentiment': post.raw_sentiment.upper(),
                'confidence': post.confidence,
                'weight': round(post.final_weight, 2),
                'score': post.score,
                'comments': post.num_comments,
                'explanation': post.explanation
            })
        
        return influential