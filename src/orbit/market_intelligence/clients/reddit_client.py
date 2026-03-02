# clients/reddit_client.py
import os

from httpx import post
import praw
import asyncio
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from orbit.market_intelligence.config.reddit_config import WEIGHTED_SUBREDDITS, WeightedSubreddit, SubredditCategory
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file

logger = logging.getLogger("Orbit")

class RedditClient:
    def __init__(self) -> None:
        self.reddit = praw.Reddit(
            client_id=os.getenv('REDDIT_CLIENT_ID'),
            client_secret=os.getenv('REDDIT_CLIENT_SECRET'),
            user_agent=os.getenv('REDDIT_USER_AGENT', 'sentiment-bot/2.0')
        )
        self.executor = ThreadPoolExecutor(max_workers=10)
        
    def fetch_weighted_posts(
        self, 
        hours_back: int = 6,
        posts_per_subreddit: int = 20
    ) -> Dict[str, Dict[str, Any]]:
        """
        Fetch posts from all weighted subreddits with metadata for weight calculation
        """
        all_posts = {}
        cutoff_time = datetime.utcnow() - timedelta(hours=hours_back)
        
        def fetch_from_subreddit(subreddit_name: str, config: WeightedSubreddit) -> List[Dict]:
            """Fetch posts from a single subreddit with metadata"""
            posts = []
            try:
                subreddit = self.reddit.subreddit(subreddit_name)
                
                # Fetch from both hot and new for better coverage
                for post in subreddit.hot(limit=posts_per_subreddit):
                    if post.stickied or post.over_18:
                        continue
                    
                    # Convert UTC timestamp to datetime
                    post_time = datetime.utcfromtimestamp(post.created_utc)
                    if post_time < cutoff_time:
                        continue

                    age_hours = (datetime.utcnow() - post_time).total_seconds() / 3600
                    
                    # Extract post data with metadata
                    post_data = {
                        'id': post.id,
                        'title': post.title,
                        'text': post.selftext[:1000] if post.selftext else '',  # Limit text length
                        'score': post.score,
                        'upvote_ratio': post.upvote_ratio,
                        'num_comments': post.num_comments,
                        'created_utc': post.created_utc,
                        'subreddit': subreddit_name,
                        'category': config.category.value,
                        'base_weight': config.base_weight,
                        'url': post.url,
                        'is_self': post.is_self,
                        # Calculate engagement score
                        'engagement_score': (post.score * 0.4 + post.num_comments * 1.5) / max(age_hours, 1)  # Avoid division by zero
                    }

                    # Raw engagement (minimum attention filter)
                    engagement_raw = post.score + 2 * post.num_comments
                    if engagement_raw < 25:
                        continue

                    if post_data['engagement_score'] < 5:
                        continue
                    
                    # Only include posts with substantial content
                    content = post_data['title'] + post_data['text']

                    if len(content) < 100:
                        continue

                    posts.append(post_data)
                        
            except Exception as e:
                logger.error(f"Error fetching from r/{subreddit_name}: {e}")
            
            return posts
        
        # Fetch posts in parallel
        futures = []
        for subreddit_name, config in WEIGHTED_SUBREDDITS.items():
            future = self.executor.submit(fetch_from_subreddit, subreddit_name, config)
            futures.append((subreddit_name, config, future))
        
        for subreddit_name, config, future in futures:
            try:
                posts = future.result(timeout=30)
                if posts:
                    all_posts[subreddit_name] = {
                        'config': config.dict(),
                        'posts': posts,
                        'stats': {
                            'count': len(posts),
                            'avg_score': sum(p['score'] for p in posts) / len(posts) if posts else 0,
                            'avg_comments': sum(p['num_comments'] for p in posts) / len(posts) if posts else 0
                        }
                    }
            except Exception as e:
                logger.error(f"Error processing r/{subreddit_name}: {e}")
        
        return all_posts
    
    def calculate_dynamic_weights(self, subreddit_data: Dict) -> Dict[str, float]:
        """Calculate dynamic weights for each subreddit based on activity"""
        weights = {}
        
        for subreddit_name, data in subreddit_data.items():
            config = WeightedSubreddit(**data['config'])
            posts_count = data['stats']['count']
            avg_score = data['stats']['avg_score']
            
            # Calculate dynamic weight
            dynamic_weight = config.calculate_dynamic_weight(posts_count, avg_score)
            weights[subreddit_name] = dynamic_weight
            
            logger.debug(f"r/{subreddit_name}: base={config.base_weight}, dynamic={dynamic_weight:.2f}, posts={posts_count}")
        
        return weights