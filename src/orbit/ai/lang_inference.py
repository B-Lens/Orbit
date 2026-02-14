# pylint: skip-file
import os
import sys
import logging
import praw
import pickle
import time
import re
import json
import traceback  # Add this import at the top of your file
import requests
import yfinance as yf
import pandas as pd
#from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
# from langgraph.prebuilt import ToolNode
from langchain_groq import ChatGroq
# from langchain_core.runnables import RunnableSequence
# import snscrape.modules.twitter as sntwitter
from langgraph.graph import StateGraph
from langchain_core.messages.ai import AIMessage
from langchain_core.tools import tool
from typing_extensions import Annotated
from typing import Dict, List, Optional, Union, Any, Callable
from langchain_core.language_models.chat_models import BaseChatModel
from langsmith import Client
from datetime import datetime
from pydantic import BaseModel, Field, field_validator
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from enum import Enum
from dotenv import load_dotenv
from orbit.utils.utils import require_env
from orbit.ai.clients.news_client import fetch_news_articles
from orbit.ai.utils.utils import MarketIndicators, initialize_llm, fetch_market_indicators

load_dotenv()  # Load environment variables from .env file

from config.config import load_config

config = load_config()

# Configure logging
logger = logging.getLogger("Orbit")


# Set up environment variables
os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGCHAIN_API_KEY"] = require_env("LANGSMITH_API_KEY")
os.environ["OPENAI_API_KEY"] = require_env("OPENAI_API_KEY")
os.environ["GROQ_API_KEY"] = require_env("GROQ_API_KEY")

# Initialize LangSmith client for observability
langsmith_client = Client()

# Keywords to track - using shorter, more focused terms
keywords = ['bitcoin', 'crypto', 'stock market', 'inflation', 'recession']

# Add Reddit API credentials to config (ensure these are set in your config file)
REDDIT_CLIENT_ID = os.getenv('REDDIT_CLIENT_ID', '')
REDDIT_CLIENT_SECRET = os.getenv('REDDIT_CLIENT_SECRET', '')
REDDIT_USER_AGENT = os.getenv('REDDIT_USER_AGENT', 'sentiment-bot/0.1')

REDDIT_SUBREDDITS = [
    'WallStreetBets', 'CryptoCurrency', 'Bitcoin', 'Economics', 'StockMarket', 'CryptoMarkets', 'Investing'
]

SENTIMENT_HISTORY_FILE = 'sentiment_history.pkl'
SENTIMENT_HISTORY_HOURS = 12

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



class SentimentHistoryEntry(BaseModel):
    """Model for sentiment history entries."""
    timestamp: float = Field(..., description="Unix timestamp")
    sentiment: str = Field(..., description="Raw sentiment string")
    
    @field_validator('timestamp')
    @classmethod
    def validate_timestamp(cls, v: float) -> float:
        """Ensure timestamp is reasonable."""
        if v < 0 or v > time.time() + 86400:  # Not negative or more than 1 day in future
            raise ValueError("Invalid timestamp")
        return v

class Message(BaseModel):
    """Model for conversation messages."""
    role: str = Field(..., description="Role of the message sender (user/assistant)")
    content: str = Field(..., description="Message content")

class SentimentState(BaseModel):
    """Main state model for the LangGraph workflow."""
    messages: List[Message] = Field(default_factory=list, description="Conversation messages")
    news_sentiment: str = Field(default="", description="News sentiment analysis result")
    social_sentiment: str = Field(default="", description="Social media sentiment analysis result")
    market_indicators: MarketIndicators = Field(default_factory=lambda: MarketIndicators(), description="Market indicators data")
    final_sentiment: Dict[str, Any] = Field(default_factory=dict, description="Final combined sentiment result")
    history_summary: Optional[str] = Field(None, description="Summary of historical sentiment")
    
    class Config:
        schema_extra = {
            "example": {
                "messages": [{"role": "user", "content": "Analyze stock market sentiment"}],
                "news_sentiment": "Sentiment: bullish, Confidence: 0.8, Explanation: Positive earnings reports",
                "social_sentiment": "Sentiment: neutral, Confidence: 0.5, Explanation: Mixed social media sentiment",
                "market_indicators": {"vix": 18.5, "fear_greed_index": 65},
                "final_sentiment": {
                    "sentiment": "bullish",
                    "confidence": 0.75,
                    "explanation": "Combined analysis",
                    "news_explanation": "Positive earnings reports",
                    "social_explanation": "Mixed social media sentiment"
                },
                "history_summary": "Recent sentiment has been mostly bullish"
            }
        }


# Now `llm` will be typed correctly
llm: Optional[BaseChatModel] = initialize_llm()

# Define prompt for sentiment analysis
sentiment_prompt = PromptTemplate(
    input_variables=["text", "reference", "history"],
    template="""
    You are an expert financial sentiment analyst.

    Reference data:
    {reference}

    Recent sentiment history:
    {history}

    Task:
    Analyze the sentiment of the following text about the stock market:
    {text}

    IMPORTANT: You must respond in exactly this format:
    Sentiment: <bullish|bearish|neutral>, Confidence: <0-1>, Explanation: <brief explanation>

    Examples:
    - Sentiment: bullish, Confidence: 0.8, Explanation: Positive news about market recovery and strong earnings reports
    - Sentiment: bearish, Confidence: 0.7, Explanation: Concerns about inflation and economic uncertainty
    - Sentiment: neutral, Confidence: 0.5, Explanation: Mixed signals with both positive and negative indicators

    If the text is empty or invalid, return: Sentiment: neutral, Confidence: 0.0, Explanation: Invalid or empty text provided
    """
)

# Generic chain for news and other long-form text
sentiment_chain = sentiment_prompt | llm if llm else None

# Specialised prompt & chain for Reddit / social-media sentiment
social_sentiment_prompt = PromptTemplate(
    input_variables=["posts", "history"],
    template="""
    You are an expert analyst of retail-investor sentiment expressed on Reddit.

    Evaluate the overall sentiment (bullish, bearish, or neutral) toward **the broad stock market** in the following collection of Reddit posts. Focus on macro/market outlook rather than individual tickers.

    Posts:
    {posts}

    Recent sentiment history for context:
    {history}

    Carefully consider Reddit slang, humour, and sarcasm when identifying sentiment.

    Respond in EXACTLY this single-line format (do not add anything else):
    Sentiment: <bullish|bearish|neutral>, Confidence: <0-1 float>, Explanation: <concise explanation mentioning the main themes>
    """
)

# Chain dedicated to social-media sentiment
social_sentiment_chain = social_sentiment_prompt | llm if llm else None

def summarize_history_for_prompt(history: List[SentimentHistoryEntry]) -> str:
    """Helper to summarize history for prompt."""
    if not history:
        return "No recent sentiment history."
    lines = []
    for entry in history[-5:]:  # last 5 entries
        ts = datetime.fromtimestamp(entry.timestamp).strftime('%Y-%m-%d %H:%M')
        lines.append(f"{ts}: {entry.sentiment}")
    return "\n".join(lines)

def summarize_history_node(state: SentimentState) -> SentimentState:
    """Summarize the recent sentiment history using the LLM."""
    history = load_sentiment_history()
    if not history:
        state.history_summary = "No recent sentiment history."
        return state
    
    # Prepare a string of recent history
    history_text = summarize_history_for_prompt(history)
    
    # Use LLM to summarize
    if llm is None:
        logger.error("LLM not available for history summary")
        state.history_summary = history_text
        return state
    
    summary_prompt = PromptTemplate(
        input_variables=["history_text"],
        template="""
        Here is a list of recent market sentiment results:
        {history_text}
        Please summarize the overall trend and any notable changes in sentiment in 2-3 sentences.
        """
    )
    summary_chain = summary_prompt | llm
    
    try:
        summary = summary_chain.invoke({"history_text": history_text})
        summary_content: str
        if isinstance(summary, str):
            summary_content = summary
        elif isinstance(summary, AIMessage):
            summary_content = str(summary.content) if summary.content else history_text
        else:
            summary_content = history_text
        state.history_summary = summary_content
    except Exception as e:
        logger.error(f"Error in history summary: {e}")
        state.history_summary = history_text
    
    return state

def news_node(state: SentimentState) -> SentimentState:
    """Analyze sentiment of news articles."""
    try:
        news_texts = fetch_news_articles.invoke("stock market sentiment")
        reference = news_texts if isinstance(news_texts, str) else "\n".join(str(item) for item in news_texts) if isinstance(news_texts, list) else str(news_texts)
        text = "Analyze the following news articles for stock market sentiment."
        history_summary = state.history_summary or "No recent sentiment history."
        
        if sentiment_chain is None:
            logger.error("LLM not available for sentiment analysis")
            state.news_sentiment = "Sentiment: neutral, Confidence: 0.0, Explanation: LLM not available"
            state.messages.append(Message(role="assistant", content=f"News sentiment: {state.news_sentiment}"))
            return state
        
        result = sentiment_chain.invoke({
            "text": text,
            "reference": reference,
            "history": history_summary
        })
        
        result_content: str
        if isinstance(result, str):
            result_content = result
        elif isinstance(result, AIMessage):
            result_content = str(result.content) if result.content else "Sentiment: neutral, Confidence: 0.0, Explanation: Invalid LLM response."
        else:
            result_content = "Sentiment: neutral, Confidence: 0.0, Explanation: Invalid LLM response."
        
        state.news_sentiment = result_content
        logger.info(f"News sentiment result: {state.news_sentiment}")
        state.messages.append(Message(role="assistant", content=f"News sentiment: {state.news_sentiment}"))
    except Exception as e:
        logger.error(f"Error in news processing: {e}")
        state.news_sentiment = f"Sentiment: neutral, Confidence: 0.0, Explanation: Error in news processing: {str(e)}"
        state.messages.append(Message(role="assistant", content=f"News sentiment: {state.news_sentiment}"))
    return state

def fetch_reddit_posts(subreddits: List[str] = REDDIT_SUBREDDITS, limit: int = 30, max_posts: int = 40) -> List[str]:
    """Fetch Reddit posts from specified subreddits in parallel batches."""
    if not all([REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT]):
        logger.error("Reddit API credentials not properly configured")
        return []
    
    try:
        reddit = praw.Reddit(
            client_id=REDDIT_CLIENT_ID,
            client_secret=REDDIT_CLIENT_SECRET,
            user_agent=REDDIT_USER_AGENT
        )
    except Exception as e:
        logger.error(f"Error initializing Reddit client: {e}")
        return []
    
    def fetch_posts_from_subreddit(subreddit_name: str) -> List[str]:
        """Fetch posts from a single subreddit."""
        posts = []
        try:
            subreddit = reddit.subreddit(subreddit_name)
            for post in subreddit.hot(limit=limit):
                if not post.stickied and not post.over_18 and post.selftext:
                    text = f"{post.title}\n{post.selftext}"
                    posts.append(text)
                    if len(posts) >= max_posts:
                        break
        except Exception as e:
            logger.error(f"Error fetching from r/{subreddit_name}: {e}")
        return posts

    # Use ThreadPoolExecutor to fetch posts from subreddits in parallel
    all_posts = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_subreddit = {executor.submit(fetch_posts_from_subreddit, sub): sub for sub in subreddits}
        for future in as_completed(future_to_subreddit):
            subreddit_posts = future.result()
            all_posts.extend(subreddit_posts)
            if len(all_posts) >= max_posts:
                break

    return all_posts[:max_posts]

def load_sentiment_history() -> List[SentimentHistoryEntry]:
    """Load sentiment history from file."""
    try:
        with open(SENTIMENT_HISTORY_FILE, 'rb') as f:
            history_data = pickle.load(f)
            # Convert to Pydantic models
            history = []
            for entry in history_data:
                if isinstance(entry, dict):
                    try:
                        history.append(SentimentHistoryEntry(**entry))
                    except Exception as e:
                        logger.warning(f"Failed to load history entry: {e}")
                elif isinstance(entry, SentimentHistoryEntry):
                    history.append(entry)
    except Exception:
        logger.info("No sentiment history found, creating new history.")
        history = []
    
    # Only keep entries from last 12 hours
    cutoff = time.time() - SENTIMENT_HISTORY_HOURS * 3600
    history = [entry for entry in history if entry.timestamp > cutoff]
    return history

def save_sentiment_history(history: List[SentimentHistoryEntry]) -> None:
    """Save sentiment history to file."""
    try:
        with open(SENTIMENT_HISTORY_FILE, 'wb') as f:
            pickle.dump([entry.model_dump() for entry in history], f)
    except Exception as e:
        logger.error(f"Error saving sentiment history: {e}")

def social_media_node(state: SentimentState) -> SentimentState:
    """Analyze sentiment of Reddit posts, batching and summarizing to avoid LLM token limits."""
    try:
        # Fetch up to 40 posts total from all subreddits
        reddit_posts = fetch_reddit_posts(limit=10, max_posts=40)
        if not reddit_posts:
            state.social_sentiment = "Sentiment: neutral, Confidence: 0.0, Explanation: No Reddit posts found."
            state.messages.append(Message(role="assistant", content=f"Social media sentiment: {state.social_sentiment}"))
            return state
        
        # Truncate, clean, and de-noise each post: title + first 250 chars of body
        url_pattern = re.compile(r"http\S+")
        cleaned_posts = []
        for post in reddit_posts:
            lines = post.strip().split("\n", 1)
            title = lines[0].strip()
            body = lines[1].strip() if len(lines) > 1 else ""
            snippet = body[:250].replace("\n", " ").strip()
            cleaned = f"{title}. {snippet}"
            cleaned = url_pattern.sub("", cleaned)  # strip URLs
            cleaned_posts.append(cleaned)
        
        # Combine all posts
        combined_text = "\n".join(cleaned_posts)
        if len(combined_text) > 5000:
            # The Reddit content is too large; summarize in batches first to keep within token limits.
            batch_size = 10
            summaries = []
            for i in range(0, len(cleaned_posts), batch_size):
                batch = cleaned_posts[i:i + batch_size]
                batch_text = "\n".join(batch)
                batch_prompt = PromptTemplate(
                    input_variables=["posts"],
                    template="""
                    Summarize the following Reddit posts about the stock market in 3-4 sentences, focusing on the overall mood and notable opinions:
                    {posts}
                    """
                )
                if llm is None:
                    summaries.append(batch_text[:300])  # Fallback if LLM not available
                    continue
                
                batch_chain = batch_prompt | llm
                summary = batch_chain.invoke({"posts": batch_text})
                if isinstance(summary, str):
                    summaries.append(summary)
                elif isinstance(summary, AIMessage):
                    summaries.append(str(summary.content) if summary.content else batch_text[:300])
                else:
                    summaries.append(batch_text[:300])  # Fallback in case of unexpected output
            # Combine all batch summaries – this will be the final text analysed for sentiment.
            reference = "\n".join(summaries)
        else:
            reference = combined_text

        history_summary = state.history_summary or "No recent sentiment history."

        # Call the dedicated social-media sentiment chain
        if social_sentiment_chain is None:
            logger.error("LLM not available for social media sentiment analysis")
            state.social_sentiment = "Sentiment: neutral, Confidence: 0.0, Explanation: LLM not available"
            state.messages.append(Message(role="assistant", content=f"Social media sentiment: {state.social_sentiment}"))
            return state
        
        result = social_sentiment_chain.invoke({
            "posts": reference,
            "history": history_summary
        })

        # Normalise result to expected format
        result_text: str
        if isinstance(result, AIMessage):
            result_text = str(result.content) if result.content else "Sentiment: neutral, Confidence: 0.0, Explanation: Invalid LLM response."
        else:
            result_text = str(result)

        # If JSON was returned, convert it
        if result_text.strip().startswith("{"):
            try:
                parsed = json.loads(result_text)
                sentiment = parsed.get("sentiment", "neutral")
                confidence = parsed.get("confidence", 0.0)
                explanation = parsed.get("explanation", "")
                result_text = f"Sentiment: {sentiment}, Confidence: {confidence}, Explanation: {explanation}"
            except json.JSONDecodeError:
                pass

        # Basic validation — ensure required fields are present
        if not ("Sentiment:" in result_text and "Confidence:" in result_text):
            result_text = "Sentiment: neutral, Confidence: 0.0, Explanation: Invalid LLM response."

        state.social_sentiment = result_text
        logger.info(f"Social sentiment result: {state.social_sentiment}")
        state.messages.append(Message(role="assistant", content=f"Social media sentiment: {state.social_sentiment}"))
        
        # Save to memory
        history = load_sentiment_history()
        history.append(SentimentHistoryEntry(
            timestamp=time.time(),
            sentiment=state.social_sentiment
        ))
        save_sentiment_history(history)
    except Exception as e:
        logger.error(f"Error in social media processing: {e}")
        logger.error("Stack trace:\n" + traceback.format_exc())  # Log the stack trace
        state.social_sentiment = f"Sentiment: neutral, Confidence: 0.0, Explanation: Error in social media processing: {str(e)}"
        state.messages.append(Message(role="assistant", content=f"Social media sentiment: {state.social_sentiment}"))
    return state

def indicators_node(state: SentimentState) -> SentimentState:
    """Fetch and process market indicators."""
    try:
        indicators = fetch_market_indicators()
        state.market_indicators = indicators
        state.messages.append(Message(role="assistant", content=f"Market indicators: {indicators.model_dump()}"))
    except Exception as e:
        logger.error(f"Error fetching indicators: {e}")
        state.market_indicators = MarketIndicators()
        state.messages.append(Message(role="assistant", content=f"Error fetching indicators: {str(e)}"))
    return state

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

def combine_sentiment_node(state: SentimentState) -> SentimentState:
    """Combine sentiments and indicators to produce final sentiment, using memory state."""
    news_result = parse_sentiment(state.news_sentiment)
    social_result = parse_sentiment(state.social_sentiment)
    indicators = state.market_indicators

    logger.info(f"News sentiment: {news_result.sentiment.value} (confidence: {news_result.confidence})")
    logger.info(f"Social sentiment: {social_result.sentiment.value} (confidence: {social_result.confidence})")
    
    # Simple heuristic to combine sentiments
    sentiment_score = 0.0
    
    # News sentiment (40% weight)
    if news_result.sentiment == SentimentType.BULLISH:
        sentiment_score += 0.4 * news_result.confidence
    elif news_result.sentiment == SentimentType.BEARISH:
        sentiment_score -= 0.4 * news_result.confidence
    
    # Social sentiment (30% weight)
    if social_result.sentiment == SentimentType.BULLISH:
        sentiment_score += 0.3 * social_result.confidence
    elif social_result.sentiment == SentimentType.BEARISH:
        sentiment_score -= 0.3 * social_result.confidence

    # Incorporate market indicators if available
    if indicators:
        # Fear & Greed Index adjustments
        if indicators.fear_greed_index and indicators.fear_greed_index > 70:
            sentiment_score += 0.3  # Greed
        elif indicators.fear_greed_index and indicators.fear_greed_index < 30:
            sentiment_score -= 0.3  # Fear
        
        # VIX adjustments
        if indicators.vix and indicators.vix > 20:
            sentiment_score -= 0.2  # High volatility = fear
        elif indicators.vix and indicators.vix < 15:
            sentiment_score += 0.2  # Low volatility = confidence

    # Combine explanations properly
    combined_explanation = ""
    if news_result.explanation.strip():
        combined_explanation += f"News: {news_result.explanation.strip()}"
    if social_result.explanation.strip():
        if combined_explanation:
            combined_explanation += " | "
        combined_explanation += f"Social: {social_result.explanation.strip()}"

    # Use memory state (last 12 hours) to adjust sentiment
    history = load_sentiment_history()
    if history:
        # Count bullish/bearish/neutral in memory
        bull, bear, neutral = 0, 0, 0
        for entry in history:
            parsed_result = parse_sentiment(entry.sentiment)
            if parsed_result.sentiment == SentimentType.BULLISH:
                bull += 1
            elif parsed_result.sentiment == SentimentType.BEARISH:
                bear += 1
            else:
                neutral += 1
        total = bull + bear + neutral
        if total > 0:
            sentiment_score += 0.1 * ((bull - bear) / total)
            # Add historical context to explanation
            if combined_explanation:
                combined_explanation += " | "
            combined_explanation += f"Historical trend: {bull} bullish, {bear} bearish, {neutral} neutral in last {SENTIMENT_HISTORY_HOURS} hours"

    # Determine final sentiment with improved thresholds
    if sentiment_score > 0.2:
        final_sentiment = SentimentType.BULLISH.value
    elif sentiment_score < -0.2:
        final_sentiment = SentimentType.BEARISH.value
    else:
        final_sentiment = SentimentType.NEUTRAL.value
    
    confidence = min(abs(sentiment_score), 1.0)
    
    # Format result with proper structure
    if combined_explanation.strip():
        result = {
            "sentiment": final_sentiment,
            "confidence": round(confidence, 2),
            "explanation": combined_explanation.strip(),
            "news_explanation": news_result.explanation.strip(),
            "social_explanation": social_result.explanation.strip()
        }
    else:
        result = {
            "sentiment": final_sentiment,
            "confidence": round(confidence, 2),
            "explanation": "Combined analysis of news and social media sentiment",
            "news_explanation": news_result.explanation.strip(),
            "social_explanation": social_result.explanation.strip()
        }
        
    logger.info(f"Final sentiment score: {sentiment_score:.3f}")
    logger.info(f"Final result: {result}")
    
    state.final_sentiment = result
    state.messages.append(Message(role="assistant", content=combined_explanation.strip()))
    return state

def inference() -> Dict[str, Any]:
    """Main inference function that runs the complete sentiment analysis workflow."""
    workflow = StateGraph(SentimentState)
    workflow.add_node("summarize_history", summarize_history_node)
    workflow.add_node("news", news_node)
    workflow.add_node("social_media", social_media_node)
    workflow.add_node("indicators", indicators_node)
    workflow.add_node("combine", combine_sentiment_node)

    workflow.add_edge("__start__", "summarize_history")
    workflow.add_edge("summarize_history", "news")
    workflow.add_edge("news", "social_media")
    workflow.add_edge("social_media", "indicators")
    workflow.add_edge("indicators", "combine")
    workflow.add_edge("combine", "__end__")

    app: Any = workflow.compile()
    initial_state = SentimentState(
        messages=[Message(role="user", content="Analyze stock market sentiment")],
        history_summary="No recent sentiment history."
    )
    
    logger.info("Starting sentiment analysis workflow")
    try:
        result = app.invoke(initial_state)
        logger.info("Workflow completed successfully")
        # Ensure result is always a SentimentState object
        if isinstance(result, dict):
            result = SentimentState(**result)
        logger.info(f"Final Sentiment: {result.final_sentiment}")
        
        # Log to LangSmith for observability
        try:
            langsmith_client.create_run(
                name="MarketSentimentAnalysis",
                inputs=initial_state.model_dump(),
                outputs=result.model_dump(),
                run_type="chain"
            )
        except Exception as e:
            logger.warning(f"Failed to log to LangSmith: {e}")
        
        FINAL_SENTIMENT: Dict[str, Any] = result.final_sentiment
        return FINAL_SENTIMENT
    except Exception as e:
        logger.error(f"Workflow failed: {e}")
        logger.error("Stack trace:\n" + traceback.format_exc())  # Log the stack trace
        return {
            'sentiment': 'NEUTRAL',
            'confidence': 0.0,
            'explanation': f"Error in sentiment analysis workflow: {str(e)}"
        }

if __name__ == "__main__":
    inference()