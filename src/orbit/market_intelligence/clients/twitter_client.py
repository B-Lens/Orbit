"""
twitter_client
==============

Thin wrapper around the ``twitter`` CLI tool (twitter-search).

The CLI is invoked as a subprocess:

    twitter search "<query>" -n <count> --json

The JSON output is parsed and returned as a list of tweet dicts that
match the schema produced by the tool (see module docstring example).

Queries are pre-configured for financial / crypto / gold topics.
"""

import json
import logging
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
load_dotenv()
logger = logging.getLogger("Orbit")

# ---------------------------------------------------------------------------
# Default search queries
# ---------------------------------------------------------------------------

TWITTER_SEARCH_QUERIES: List[Dict[str, Any]] = [
    {"query": "crypto market", "count": 10, "weight": 1.0},
    {"query": "bitcoin BTC", "count": 10, "weight": 1.0},
    {"query": "gold XAUUSD market", "count": 10, "weight": 0.9},
    {"query": "stock market economy", "count": 10, "weight": 0.8},
    {"query": "Federal Reserve interest rates", "count": 10, "weight": 0.9},
    {"query": "inflation CPI recession", "count": 10, "weight": 0.85},
    {"query": "ethereum ETH altcoin", "count": 10, "weight": 0.8},
]

# CLI timeout in seconds
_CLI_TIMEOUT: int = 30


class TwitterClient:
    """Fetch financial tweets using the ``twitter`` CLI tool.

    Args:
        queries: List of query config dicts, each with keys:
                 ``query`` (str), ``count`` (int), ``weight`` (float 0-1).
                 Defaults to :data:`TWITTER_SEARCH_QUERIES`.
        cli_timeout: Seconds before a single CLI call is killed.
    """

    def __init__(
        self,
        queries: Optional[List[Dict[str, Any]]] = None,
        cli_timeout: int = _CLI_TIMEOUT,
    ) -> None:
        self.queries: List[Dict[str, Any]] = queries or TWITTER_SEARCH_QUERIES
        self.cli_timeout: int = cli_timeout

    # ------------------------------------------------------------------
    # LOW-LEVEL FETCH
    # ------------------------------------------------------------------

    def _run_search(self, query: str, count: int) -> List[Dict[str, Any]]:
        """
        Execute ``twitter search "<query>" -n <count> --json`` and return
        the parsed tweet list.

        Args:
            query: Search string passed to the CLI.
            count: Maximum number of tweets to retrieve.

        Returns:
            List of raw tweet dicts from the CLI JSON output.
            Returns an empty list on any error.
        """
        cmd = ["twitter", "search", query, "-n", str(count), "--json"]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.cli_timeout,
            )
            if result.returncode != 0:
                logger.warning(
                    f"twitter CLI non-zero exit ({result.returncode}) "
                    f"for query='{query}': {result.stderr.strip()}"
                )
                return []

            payload = json.loads(result.stdout)
            if not payload.get("ok"):
                logger.error(
                    f"twitter CLI returned ok=false for query='{query}'"
                )
                return []

            return payload.get("data", [])

        except subprocess.TimeoutExpired:
            logger.exception(f"twitter CLI timed out for query='{query}'")
            return []
        except json.JSONDecodeError as exc:
            logger.exception(f"twitter CLI JSON parse error for query='{query}'")
            return []
        except FileNotFoundError:
            logger.exception(
                "twitter CLI not found. Install it with: pip install twitter-search"
            )
            return []
        except Exception as exc:
            logger.exception(f"Unexpected error fetching tweets for query='{query}'")
            return []

    # ------------------------------------------------------------------
    # DEDUPLICATION
    # ------------------------------------------------------------------

    @staticmethod
    def _deduplicate(tweets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove duplicate tweets by ``id``."""
        seen: set = set()
        unique: List[Dict[str, Any]] = []
        for tweet in tweets:
            tid = tweet.get("id")
            if tid and tid not in seen:
                seen.add(tid)
                unique.append(tweet)
        return unique

    # ------------------------------------------------------------------
    # ENGAGEMENT SCORE
    # ------------------------------------------------------------------

    @staticmethod
    def _engagement_score(tweet: Dict[str, Any]) -> float:
        """
        Compute a simple engagement score from tweet metrics.

        Formula:
            likes * 1.0 + retweets * 2.0 + quotes * 1.5 + replies * 0.5
            + views * 0.001

        Args:
            tweet: Raw tweet dict.

        Returns:
            Float engagement score (higher = more influential).
        """
        m = tweet.get("metrics") or {}
        return (
            float(m.get("likes", 0)) * 1.0
            + float(m.get("retweets", 0)) * 2.0
            + float(m.get("quotes", 0)) * 1.5
            + float(m.get("replies", 0)) * 0.5
            + float(m.get("views", 0)) * 0.001
        )

    # ------------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------------

    def fetch_tweets(self) -> List[Dict[str, Any]]:
        """
        Fetch tweets for all configured queries, deduplicate, and attach
        ``_weight`` and ``_engagement_score`` metadata to each tweet.

        Returns:
            Deduplicated list of tweet dicts enriched with:
            - ``_weight``: query-level weight (float)
            - ``_engagement_score``: computed engagement score (float)
        """
        all_tweets: List[Dict[str, Any]] = []

        for cfg in self.queries:
            query: str = cfg["query"]
            count: int = cfg.get("count", 20)
            weight: float = cfg.get("weight", 1.0)

            tweets = self._run_search(query, count)
            logger.info(f"Twitter: fetched {len(tweets)} tweets for query='{query}'")

            for tweet in tweets:
                tweet["_weight"] = weight
                tweet["_engagement_score"] = self._engagement_score(tweet)

            all_tweets.extend(tweets)

        deduped = self._deduplicate(all_tweets)
        logger.info(f"Twitter: {len(deduped)} unique tweets after deduplication")
        return deduped

    def fetch_tweets_since(
        self, since: Optional[datetime]
    ) -> List[Dict[str, Any]]:
        """
        Fetch tweets and filter to only those created after *since*.

        Args:
            since: UTC-aware or naive datetime threshold.
                   When ``None`` all fetched tweets are returned.

        Returns:
            Filtered list of tweet dicts (same enriched format as
            :meth:`fetch_tweets`).
        """
        tweets = self.fetch_tweets()

        if since is None:
            return tweets

        filtered: List[Dict[str, Any]] = []
        for tweet in tweets:
            created_iso: Optional[str] = tweet.get("createdAtISO")
            if not created_iso:
                # Keep tweets with no timestamp rather than silently drop them
                filtered.append(tweet)
                continue
            try:
                tweet_dt = datetime.fromisoformat(created_iso)
                # Make both tz-naive for comparison if needed
                if tweet_dt.tzinfo is not None and since.tzinfo is None:
                    tweet_dt = tweet_dt.replace(tzinfo=None)
                elif tweet_dt.tzinfo is None and since.tzinfo is not None:
                    since = since.replace(tzinfo=None)
                if tweet_dt > since:
                    filtered.append(tweet)
            except ValueError:
                filtered.append(tweet)

        logger.info(
            f"Twitter: {len(filtered)} tweets after filtering since={since}"
        )
        return filtered
