import os
import logging
import requests
from datetime import datetime, timezone
from typing import Optional
from langchain_core.tools import tool

from dotenv import load_dotenv

from orbit.utils.utils import to_ist

load_dotenv()

NEWSDATA_API_KEY = os.getenv('NEWSDATA_API_KEY')

logger = logging.getLogger("Orbit")

# Module-level cache for deduplication
_seen_article_ids: set = set()
_last_fetch_time: Optional[datetime] = None


def fetch_news_articles_since(since_aware: Optional[datetime] = None) -> tuple[str, list[str], Optional[datetime]]:
    """
    Fetch recent news articles, returning only articles not seen before.

    Args:
        since: Optional datetime. Articles published before this time are skipped.
               Also uses module-level seen-ID cache to deduplicate across calls.

    Returns:
        Tuple of (combined_text, list_of_new_article_ids).
        combined_text is empty string if no new articles found.
    """
    global _seen_article_ids, _last_fetch_time

    if not NEWSDATA_API_KEY:
        logger.error("NEWSDATA_API_KEY not configured")
        return "", [], _last_fetch_time

    query = "bitcoin OR crypto OR stock market"
    url = (
        f"https://newsdata.io/api/1/news"
        f"?apikey={NEWSDATA_API_KEY}"
        f"&q={query}"
        f"&language=en"
    )

    logger.info(f"NewsData API URL length: {len(url)} characters")

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch news: {e}")
        return "", [], _last_fetch_time

    try:
        news_data = response.json()
    except ValueError as e:
        logger.error(f"Failed to parse News JSON: {e}")
        return "", [], _last_fetch_time

    if "error" in news_data:
        logger.error("Error in News API response from NewsData API")
        return "", [], _last_fetch_time

    articles = []
    new_ids = []

    if since_aware:
        logger.info(f"Filtering news articles published after {since_aware.isoformat()}")
    else:
        logger.info("No 'since' timestamp provided, using seen-IDs cache for deduplication")

    for article in news_data.get("results", []):
        article_id = article.get("article_id") or article.get("link") or ""

        # Skip already-seen articles
        if article_id and article_id in _seen_article_ids:
            continue

        try:
            pub_date_str = article.get("pubDate") or ""
            pub_dt = datetime.strptime(pub_date_str, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
            pub_dt = to_ist(pub_dt)
            if since_aware is not None and pub_dt <= since_aware:
                continue    # Skip articles older than `since` if provided
        except ValueError:
            continue  # If we can't parse, exclude the article


        logger.info(f"Article '{article.get('title', '')}' published at {pub_dt.isoformat()}")
                    
        if _last_fetch_time is None or pub_dt > _last_fetch_time:
            _last_fetch_time = pub_dt

        title = article.get("title") or ""
        description = article.get("description") or ""
        full_text = f"{title.strip()}. {description.strip()}"

        if full_text.strip():
            articles.append(full_text)
            if article_id:
                new_ids.append(article_id)

    # Update the seen-IDs cache
    _seen_article_ids.update(new_ids)

    articles_text = "\n\n".join(articles)

    if not articles_text.strip():
        logger.info("No new news articles found since last fetch.")
        return "", [], _last_fetch_time

    logger.info(f"Fetched {len(articles)} new news articles.")
    return articles_text, new_ids, _last_fetch_time


@tool
def fetch_news_articles(query: str) -> str:
    """Fetch recent news articles and extract sentiment."""
    if not NEWSDATA_API_KEY:
        logger.error("NEWSDATA_API_KEY not configured")
        return "No valid news content found."

    query = (
                "bitcoin OR crypto OR stock market OR"
                "gold OR xau OR"
                "federal reserve OR fed OR interest rate OR"
                "inflation OR cpi OR"
                "central bank OR monetary policy OR liquidity OR"
                "geopolitics OR war OR sanctions"
             )
    url = (
        f"https://newsdata.io/api/1/news"
        f"?apikey={NEWSDATA_API_KEY}"
        f"&q={query}"
        f"&language=en"
    )

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

    if "error" in news_data:
        logger.error("Error in News API response from NewsData API")
        return "No valid news content found."

    articles = []
    for article in news_data.get("results", []):
        title = article.get("title") or ""
        description = article.get("description") or ""
        full_text = f"{title.strip()}. {description.strip()}"
        if full_text.strip():
            articles.append(full_text)

    articles_text = "\n\n".join(articles)
    if not articles_text.strip():
        logger.warning("No valid news content found.")
        return "No valid news content found."

    logger.info(f"Successfully fetched {len(articles)} news articles")
    return articles_text
