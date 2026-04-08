from typing import List, Dict, Any, Optional
import feedparser
from datetime import datetime, timezone, timedelta
from dateutil import parser as dateparser
from orbit.utils.utils import to_ist

News = Dict[str, Any]

RSS_FEEDS = {
    "macro": [
        "https://www.investing.com/rss/news_285.rss",
        "https://www.investing.com/rss/news_25.rss",
        "https://www.investing.com/rss/news_1.rss",
    ],

    "crypto": [
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss"
    ],

    "global": [
        "http://feeds.bbci.co.uk/news/world/rss.xml",
        "http://rss.cnn.com/rss/edition_world.rss"
    ],

    "finance": [
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=GC=F&region=US&lang=en-US",
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=DX-Y.NYB&region=US&lang=en-US"
    ]
}

def parse_entry(entry: Any, source: str) -> News:
    published = None

    if hasattr(entry, "published"):
        try:
            published = dateparser.parse(entry.published)

            if published and published.tzinfo is not None:
                published = published.astimezone(timezone.utc).replace(tzinfo=None)

        except Exception:
            pass

    return {
        "title": entry.get("title", ""),
        "summary": entry.get("summary", ""),
        "link": entry.get("link", ""),
        "published": published,
        "source": source
    }


def fetch_all_rss_news() -> List[News]:
    news: List[News] = []

    for category, feeds in RSS_FEEDS.items():
        for url in feeds:
            try:
                feed = feedparser.parse(url)

                for entry in feed.entries:
                    parsed = parse_entry(entry, category)

                    if parsed["title"] and parsed["published"]:
                        dt = parsed["published"]

                        # make timezone-aware first
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)

                        parsed["published"] = to_ist(dt)
                        news.append(parsed)

            except Exception as e:
                print(f"Failed feed: {url} -> {e}")

    return news


def deduplicate(news: List[News]) -> List[News]:
    seen = set()
    unique: List[News] = []

    for n in news:
        key = n["title"].lower().strip()

        if key not in seen:
            seen.add(key)
            unique.append(n)

    return unique


def sort_latest(news: List[News]) -> List[News]:
    return sorted(
        news,
        key=lambda x: x["published"] or datetime.min,
        reverse=True
    )


def filter_recent(news: List[News], hours: int = 6) -> List[News]:
    now = datetime.utcnow()
    filtered: List[News] = []

    for n in news:
        if n["published"]:
            diff = now - n["published"]
            if diff.total_seconds() <= hours * 3600:
                filtered.append(n)

    return filtered


def format_for_llm(news: List[News], limit: int = 20) -> str:
    parts: List[str] = []

    for n in news[:limit]:
        text = f"""
        Source: {n['source']}
        Time: {n['published']}
        Title: {n['title']}
        Summary: {n['summary']}
        Link: {n['link']}
        """
        parts.append(text.strip())

    return "\n\n".join(parts)