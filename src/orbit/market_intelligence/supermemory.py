"""
supermemory.py
==============

SDK-based wrapper around the Supermemory API.

Responsibilities
----------------
- Store sentiment results as memory documents.
- Search recent memories and return structured objects.
- Build context strings for LLM prompts.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from supermemory import Supermemory

logger = logging.getLogger("Orbit")


# ------------------------------------------------------------------
# Data Model
# ------------------------------------------------------------------

@dataclass
class SentimentMemory:
    sentiment: str          # BULLISH | BEARISH | NEUTRAL
    confidence: float
    explanation: str
    source: str             # "full_analysis" | "news_update" | "drift"
    timestamp: str          # ISO-8601


# ------------------------------------------------------------------
# Client
# ------------------------------------------------------------------

class SupermemoryClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        container_id: str = "orbit-sentiment",
    ) -> None:
        self._api_key = api_key or os.environ.get("SUPERMEMORY_API_KEY")
        self._container_id = container_id
        self._enabled = bool(self._api_key)

        if self._enabled:
            self.client = Supermemory(api_key=self._api_key)
        else:
            logger.warning(
                "SupermemoryClient: SUPERMEMORY_API_KEY not set — memory disabled."
            )

    # ------------------------------------------------------------------
    # Add Memory
    # ------------------------------------------------------------------

    def add_sentiment(
        self,
        sentiment: str,
        confidence: float,
        explanation: str,
        source: str = "full_analysis",
        extra_tags: Optional[List[str]] = None,
    ) -> bool:
        if not self._enabled:
            return False

        now_iso = datetime.now(timezone.utc).isoformat()
        tags = [f"source:{source}", f"sentiment:{sentiment}"] + (extra_tags or [])

        try:
            self.client.memories.add(
                content=f"Explanation: {explanation}",
                metadata={
                    "sentiment": sentiment,
                    "confidence": round(confidence, 2),
                    "source": source,
                    "timestamp": now_iso,
                },
                container_tags=[self._container_id],
                tags=tags,
            )
            logger.info(f"Stored sentiment memory ({sentiment}, {source})")
            return True

        except Exception as exc:
            logger.exception(f"Supermemory SDK error (add): {exc}")
            return False

    # ------------------------------------------------------------------
    # Search Memory
    # ------------------------------------------------------------------

    def search_recent_sentiments(
        self,
        query: str = "recent market sentiment analysis",
        limit: int = 8,
    ) -> List[SentimentMemory]:
        if not self._enabled:
            return []

        try:
            results = self.client.memories.search(
                query=query,
                limit=limit,
                container_tags=[self._container_id],
            )

            memories: List[SentimentMemory] = []

            for item in results:
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
            logger.exception(f"Supermemory SDK error (search): {exc}")
            return []

    # ------------------------------------------------------------------
    # Build Context String
    # ------------------------------------------------------------------

    def build_context_string(
        self,
        query: str = "recent market sentiment analysis",
        limit: int = 8,
    ) -> str:
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

    # ------------------------------------------------------------------
    # Dominant Sentiment
    # ------------------------------------------------------------------

    def dominant_sentiment_from_memory(
        self,
        query: str = "recent market sentiment",
        limit: int = 10,
    ) -> Optional[str]:
        memories = self.search_recent_sentiments(query=query, limit=limit)

        if not memories:
            return None

        counts = {"BULLISH": 0.0, "BEARISH": 0.0, "NEUTRAL": 0.0}

        for m in memories:
            label = m.sentiment.upper()
            if label in counts:
                counts[label] += m.confidence

        dominant = max(counts, key=lambda k: counts[k])

        logger.info(f"Dominant sentiment: {dominant}, counts={counts}")
        return dominant
