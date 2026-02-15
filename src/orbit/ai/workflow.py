# enhanced_workflow.py
import asyncio
import os
import time
import logging
from datetime import datetime
from typing import Dict, Any, List

from orbit.ai.clients.reddit_client import RedditClient
from orbit.ai.clients.news_client import fetch_news_articles
from orbit.ai.analysis.reddit_sentiment import WeightedRedditAnalyzer
from orbit.ai.models.mongodb_models import MongoDBManager, SentimentRecord
from orbit.ai.config.reddit_config import WEIGHTED_SUBREDDITS
from orbit.ai.utils.utils import fetch_market_indicators, parse_sentiment, SentimentType
from orbit.utils.utils import require_env
from langsmith import traceable

os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGCHAIN_API_KEY"] = require_env("LANGSMITH_API_KEY")


logger = logging.getLogger("Orbit")

class SentimentWorkflow:
    def __init__(self, llm):
        self.llm = llm
        self.reddit_client = RedditClient()
        self.reddit_analyzer = WeightedRedditAnalyzer(llm)
        self.mongodb = MongoDBManager()
        
        self.legacy_functions = {
            'fetch_news': fetch_news_articles,
            'fetch_indicators': fetch_market_indicators,
            'parse_sentiment': parse_sentiment
        }

    @traceable(name="fetch_reddit_posts")
    def fetch_reddit(self, hours_back=6, posts_per_subreddit=15):
        return self.reddit_client.fetch_weighted_posts(
            hours_back=hours_back,
            posts_per_subreddit=posts_per_subreddit
        )

    @traceable(name="calculate_dynamic_weights")
    def calculate_weights(self, reddit_posts_data):
        return self.reddit_client.calculate_dynamic_weights(
            reddit_posts_data
        )

    @traceable(name="aggregate_reddit_sentiment")
    def aggregate_sentiment(self, sentiments):
        return self.reddit_analyzer.aggregate_weighted_sentiment(sentiments)

    @traceable(name="fetch_news")
    def fetch_news(self, topic="crypto market"):
        return self.legacy_functions["fetch_news"].invoke(topic)

    @traceable(name="fetch_indicators")
    def fetch_indicators(self):
        return self.legacy_functions["fetch_indicators"]()

    @traceable(name="save_to_mongodb")
    def save_db(self, *args, **kwargs):
        return self._save_to_database(*args, **kwargs)

    
    async def run_analysis(self) -> Dict[str, Any]:
        """Run complete enhanced sentiment analysis"""
        start_time = time.time()
        
        try:
            # Step 1: Fetch weighted Reddit posts
            logger.info("Fetching weighted Reddit posts...")
            reddit_posts_data: Dict[str, Dict[str, Any]] = self.fetch_reddit(
                hours_back=6,
                posts_per_subreddit=15
            )
            
            # Step 2: Calculate dynamic weights
            logger.info("Calculating dynamic weights...")
            dynamic_weights = self.calculate_weights(reddit_posts_data)
            
            # Step 3: Analyze each post with weights
            logger.info("Analyzing Reddit posts...")
            all_sentiments = []
            
            for subreddit_name, data in reddit_posts_data.items():
                weight = dynamic_weights.get(subreddit_name, 0.5)
                
                for post in data['posts']:
                    sentiment = await self.reddit_analyzer.analyze_post_sentiment(
                        post, weight
                    )
                    all_sentiments.append(sentiment)
            
            # Step 4: Aggregate weighted sentiments
            logger.info("Aggregating weighted sentiments...")
            reddit_result = self.aggregate_sentiment(all_sentiments)
            print(f"Aggregated Reddit Sentiment: {reddit_result}")
            
            # Step 5: Get top influential posts
            top_posts = self.reddit_analyzer.get_top_influential_posts(all_sentiments)

            print(f"Top Influential Posts: {top_posts}")
            
            # Step 6: Run legacy analyses (news, indicators)
            logger.info("Running legacy analyses...")
            news_text = self.fetch_news(topic="crypto market")
            indicators = self.fetch_indicators()
            
            # Step 7: Combine all results
            combined_result = self._combine_results(
                reddit_result, news_text, indicators
            )
            
            # Step 8: Save to MongoDB
            logger.info("Saving to MongoDB...")
            record_id = self.save_db(
                reddit_result=reddit_result,
                top_posts=top_posts,
                news_text=news_text,
                indicators=indicators,
                combined=combined_result,
                processing_time=int((time.time() - start_time) * 1000)
            )
            
            # Step 9: Calculate trends and signals
            logger.info("Calculating trends...")
            trend = self.mongodb.calculate_trends(hours=24)
            signal = self.mongodb.get_trading_signals()
            
            # Final result
            final_result = {
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'database_id': record_id,
                'sentiment': combined_result,
                'reddit_analysis': {
                    'weighted_score': reddit_result['overall_score'],
                    'label': reddit_result['sentiment_label'],
                    'confidence': reddit_result['confidence'],
                    'posts_analyzed': reddit_result['total_posts_analyzed'],
                    'category_breakdown': reddit_result['category_breakdown'],
                    'top_influential_posts': top_posts
                },
                'market_indicators': {
                    'vix': indicators.vix,
                    'fear_greed_index': indicators.fear_greed_index
                },
                'trends': trend.dict() if trend else None,
                'trading_signal': signal,
                'processing_time_ms': int((time.time() - start_time) * 1000)
            }
            
            logger.info(f"Analysis complete. Final sentiment: {combined_result['sentiment_label']}")
            return final_result
            
        except Exception as e:
            logger.error(f"Enhanced workflow failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {
                'success': False,
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }
    
    def _combine_results(self, reddit_result, news_text, indicators):
        """Combine Reddit results with legacy analyses"""
        
        # Parse news sentiment (simplified)
        news_sentiment = self.legacy_functions['parse_sentiment'](news_text)
        
        # Combine scores with weights
        reddit_weight = 0.6  # 60% weight to Reddit
        news_weight = 0.4     # 40% weight to news
        
        combined_score = (
            reddit_result['overall_score'] * reddit_weight +
            news_sentiment.confidence * news_weight * (
                1 if news_sentiment.sentiment == SentimentType.BULLISH else
                -1 if news_sentiment.sentiment == SentimentType.BEARISH else 0
            )
        )
        
        # Determine label
        if combined_score > 0.2:
            label = 'BULLISH'
        elif combined_score < -0.2:
            label = 'BEARISH'
        else:
            label = 'NEUTRAL'
        
        return {
            'score': round(combined_score, 3),
            'label': label,
            'confidence': round(
                (reddit_result['confidence'] * reddit_weight + 
                 news_sentiment.confidence * news_weight), 2
            )
        }
    
    def _save_to_database(
        self,
        reddit_result: Dict,
        top_posts: List,
        news_text: str,
        indicators,
        combined: Dict,
        processing_time: int
    ) -> str:
        """Save analysis results to MongoDB"""
        
        record = SentimentRecord(
            overall_score=combined['score'],
            sentiment_label=combined['label'],
            confidence=combined['confidence'],
            reddit_weighted_score=reddit_result['overall_score'],
            reddit_category_breakdown=reddit_result['category_breakdown'],
            reddit_posts_analyzed=reddit_result['total_posts_analyzed'],
            top_influential_posts=top_posts,
            news_sentiment={
                'summary': news_text[:500] if news_text else '',
                'source': 'newsdata.io'
            },
            market_indicators={
                'vix': indicators.vix,
                'fear_greed_index': indicators.fear_greed_index
            },
            processing_time_ms=processing_time
        )
        
        return self.mongodb.save_sentiment(record)