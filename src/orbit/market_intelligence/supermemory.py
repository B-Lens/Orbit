"""
supermemory.py
==============

Thin wrapper around the Supermemory REST API
(https://docs.supermemory.ai).

Responsibilities
----------------
- Store a sentiment result (label + confidence + explanation + source) as a
  memory document so that future LLM calls can be grounded in recent history.
- Search recent memories by a free-text query and return them as a
  ready-to-embed context string.
- Provide a lightweight ``SentimentMemory`` dataclass that the rest of the
  codebase can pass around without importing ``requests`` directly.

Environment variables
---------------------
SUPERMEMORY_API_KEY : str
    API key issued by Supermemory.  When absent the client degrades
    gracefully — every write is a no-op and every search returns an empty
    string so the rest of the pipeline is unaffected.
SUPERMEMORY_BASE_URL : str  (optional)
    Override the default ``https://api.supermemory.ai/v3`` endpoint.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

import requests

logger = logging.getLogger("Orbit")

_DEFAULT_BASE_URL = "https://api.supermemory.ai/v3"
_TIMEOUT = 10  # seconds per HTTP call


@dataclass
class SentimentMemory:
    """A single sentiment memory entry returned from Supermemory search."""

    sentiment: str          # BULLISH | BEARISH | NEUTRAL
    confidence: float
    explanation: str
    source: str             # "full_analysis" | "news_update" | "drift"
    timestamp: str          # ISO-8601


class SupermemoryClient:
    """REST client for the Supermemory API.

    Args:
        api_key: Supermemory API key.  Falls back to the
            ``SUPERMEMORY_API_KEY`` environment variable.
        base_url: API base URL.  Falls back to the
            ``SUPERMEMORY_BASE_URL`` environment variable, then the
            hard-coded default.
        container_id: Optional Supermemory container (collection) tag used
            to namespace all memories for this application.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        container_id: str = "orbit-sentiment",
    ) -> None:
        self._api_key: Optional[str] = api_key or os.environ.get("SUPERMEMORY_API_KEY")
        self._base_url: str = (
            base_url
            or os.environ.get("SUPERMEMORY_BASE_URL", _DEFAULT_BASE_URL)
        ).rstrip("/")
        self._container_id = container_id
        self._enabled: bool = bool(self._api_key)

        if not self._enabled:
            logger.warning(
                "SupermemoryClient: SUPERMEMORY_API_KEY not set — "
                "memory features are disabled."
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_sentiment(
        self,
        sentiment: str,
        confidence: float,
        explanation: str,
        source: str = "full_analysis",
        extra_tags: Optional[List[str]] = None,
    ) -> bool:
        """Store a sentiment result as a Supermemory document.

        Args:
            sentiment: ``BULLISH``, ``BEARISH``, or ``NEUTRAL``.
            confidence: Confidence score 0-1.
            explanation: Human-readable reasoning.
            source: Which part of the pipeline produced this result.
            extra_tags: Additional metadata tags.

        Returns:
            ``True`` on success, ``False`` on failure / disabled.
        """
        if not self._enabled:
            return False

        now_iso = datetime.now(timezone.utc).isoformat()
        tags = [f"source:{source}", f"sentiment:{sentiment}"] + (extra_tags or [])

        content = (
            f"[{now_iso}] Sentiment={sentiment} "
            f"Confidence={confidence:.3f} "
            f"Source={source}\n"
            f"Explanation: {explanation}"
        )

        payload = {
            "content": content,
            "metadata": {
                "sentiment": sentiment,
                "confidence": confidence,
                "source": source,
                "timestamp": now_iso,
            },
            "containerTags": [self._container_id],
            "tags": tags,
        }

        try:
            resp = requests.post(
                f"{self._base_url}/memories",
                json=payload,
                headers=self._headers(),
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            logger.debug(f"Supermemory: stored sentiment memory ({sentiment}, {source})")
            return True
        except Exception as exc:
            logger.warning(f"Supermemory: failed to store memory — {exc}")
            return False

    def search_recent_sentiments(
        self,
        query: str = "recent market sentiment analysis",
        limit: int = 8,
    ) -> List[SentimentMemory]:
        """Search Supermemory for recent sentiment memories.

        Args:
            query: Free-text search query.
            limit: Maximum number of memories to return.

        Returns:
            List of :class:`SentimentMemory` objects, newest first.
        """
        if not self._enabled:
            return []

        params = {
            "q": query,
            "limit": limit,
            "containerTags": self._container_id,
        }

        try:
            resp = requests.get(
                f"{self._base_url}/memories/search",
                params=params,
                headers=self._headers(),
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", data) if isinstance(data, dict) else data

            memories: List[SentimentMemory] = []
            for item in results[:limit]:
                meta = item.get("metadata", {})
                memories.append(
                    SentimentMemory(
                        sentiment=meta.get("sentiment", "NEUTRAL"),
                        confidence=float(meta.get("confidence", 0.5)),
                        explanation=item.get("content", ""),
                        source=meta.get("source", "unknown"),
                        timestamp=meta.get("timestamp", ""),
                    )
                )
            return memories
        except Exception as exc:
            logger.warning(f"Supermemory: search failed — {exc}")
            return []

    def build_context_string(
        self,
        query: str = "recent market sentiment analysis",
        limit: int = 8,
    ) -> str:
        """Return a formatted string of recent memories ready to embed in a
        prompt.

        Args:
            query: Free-text search query forwarded to :meth:`search_recent_sentiments`.
            limit: Maximum number of memories to include.

        Returns:
            Multi-line string, or empty string when no memories exist.
        """
        memories = self.search_recent_sentiments(query=query, limit=limit)
        if not memories:
            return ""

        lines = ["=== Recent Sentiment Memory (newest first) ==="]
        for m in memories:
            lines.append(
                f"- [{m.timestamp}] {m.sentiment} "
                f"(conf={m.confidence:.2f}, source={m.source}): "
                f"{m.explanation[:200]}"
            )
        lines.append("=== End of Memory ===")
        return "\n".join(lines)

    def dominant_sentiment_from_memory(
        self,
        query: str = "recent market sentiment",
        limit: int = 10,
    ) -> Optional[str]:
        """Return the majority sentiment label from recent memories.

        Useful for drift suppression: if memory strongly says BULLISH and the
        new signal says BEARISH with moderate confidence, the drift may be
        noise.

        Args:
            query: Search query.
            limit: How many memories to consider.

        Returns:
            ``"BULLISH"``, ``"BEARISH"``, ``"NEUTRAL"``, or ``None`` when
            there are no memories.
        """
        memories = self.search_recent_sentiments(query=query, limit=limit)
        if not memories:
            return None

        counts: dict[str, float] = {"BULLISH": 0.0, "BEARISH": 0.0, "NEUTRAL": 0.0}
        for m in memories:
            label = m.sentiment.upper()
            if label in counts:
                # Weight by confidence so high-confidence memories count more
                counts[label] += m.confidence

        dominant = max(counts, key=lambda k: counts[k])
        logger.debug(
            f"Supermemory dominant sentiment: {dominant} "
            f"(weighted counts={counts})"
        )
        return dominant
