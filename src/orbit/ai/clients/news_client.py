import os
import logging
import requests
from langchain_core.tools import tool

from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file

NEWSDATA_API_KEY = os.getenv('NEWSDATA_API_KEY')

logger = logging.getLogger(__name__)

@tool
def fetch_news_articles(query: str) -> str:
    """Fetch recent news articles and extract sentiment."""
    if not NEWSDATA_API_KEY:
        logger.error("NEWSDATA_API_KEY not configured")
        return "No valid news content found."
    
    # Use a more focused query to stay within API limits
    query = "bitcoin OR crypto OR stock market"
    url = f'https://newsdata.io/api/1/news?apikey={NEWSDATA_API_KEY}&q={query}&language=en'
    
    # Log URL length for debugging
    logger.info(f"NewsData API URL length: {len(url)} characters")
    logger.info(f"Query: {query}")
    
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch news: {e}")
        return "No valid news content found."
    
    try:
        news_data = response.json()
    except ValueError as e:
        logger.error(f"Failed to parse News JSON: {e}")
        return "No valid news content found."

    if 'error' in news_data:
        logger.error("Error in News API response from NewsData API")
        return "No valid news content found."
    
    articles = []
    for article in news_data.get('results', []):
        title = article.get('title') or ''
        description = article.get('description') or ''
        full_text = f"{title.strip()}. {description.strip()}"
        if full_text.strip():  # avoid empty entries
            articles.append(full_text)

    # Join the articles into a single chunk of text
    articles_text = "\n\n".join(articles)
    if not articles_text.strip():
        logger.warning("No valid news content found.")
        return "No valid news content found."
    
    logger.info(f"Successfully fetched {len(articles)} news articles")
    return articles_text