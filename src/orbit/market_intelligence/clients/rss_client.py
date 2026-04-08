import feedparser
from datetime import datetime, timezone, timedelta
from dateutil import parser as dateparser
from orbit.utils.utils import to_ist


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


# -----------------------------
# Parse RSS entry (timezone safe)
# -----------------------------
def parse_entry(entry, source):
    published = None

    if hasattr(entry, "published"):
        try:
            published = dateparser.parse(entry.published)

            # normalize timezone → avoid sorting error
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


# -----------------------------
# Fetch all news
# -----------------------------
def fetch_all_rss_news():
    news = []

    for category, feeds in RSS_FEEDS.items():
        for url in feeds:
            try:
                feed = feedparser.parse(url)

                for entry in feed.entries:
                    parsed = parse_entry(entry, category)

                    if parsed["title"]:
                        news.append(parsed)
                        parsed["published"] = to_ist(parsed["published"]) if parsed["published"] else None

            except Exception as e:
                print(f"Failed feed: {url} -> {e}")

    return news


# -----------------------------
# Remove duplicates
# -----------------------------
def deduplicate(news):
    seen = set()
    unique = []

    for n in news:
        key = n["title"].lower().strip()

        if key not in seen:
            seen.add(key)
            unique.append(n)

    return unique


# -----------------------------
# Sort latest first
# -----------------------------
def sort_latest(news):
    return sorted(
        news,
        key=lambda x: x["published"] or datetime.min,
        reverse=True
    )


# -----------------------------
# Filter recent news
# -----------------------------
def filter_recent(news, hours=6):
    now = datetime.utcnow()

    filtered = []

    for n in news:
        if n["published"]:
            diff = now - n["published"]
            if diff.total_seconds() <= hours * 3600:
                filtered.append(n)

    return filtered


# -----------------------------
# Format for LLM
# -----------------------------
def format_for_llm(news, limit=20):
    parts = []

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
