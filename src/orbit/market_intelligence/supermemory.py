"""
supermemory.py
==============

REST-based wrapper around the Supermemory API (v4).

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
from typing import List, Optional, Dict

import requests

logger = logging.getLogger("Orbit")

_BASE_URL = "https://api.supermemory.ai/v4"
_TIMEOUT = 10


# ------------------------------------------------------------------
# Data Model
# ------------------------------------------------------------------

@dataclass
class SentimentMemory:
    sentiment: str
    confidence: float
    explanation: str
    source: str
    timestamp: str


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

        if not self._enabled:
            logger.warning(
                "SupermemoryClient: SUPERMEMORY_API_KEY not set — memory disabled."
            )

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

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

        memory = {
            "content": f"Explanation: {explanation}",
            "metadata": {
                "sentiment": sentiment,
                "confidence": round(confidence, 2),
                "source": source,
                "timestamp": now_iso,
            },
            "tags": [f"source:{source}", f"sentiment:{sentiment}"] + (extra_tags or []),
        }

        payload = {
            "memories": [memory],
            "containerTag": self._container_id,
        }

        try:
            resp = requests.post(
                f"{_BASE_URL}/memories",
                json=payload,
                headers=self._headers(),
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()

            logger.info(f"Stored sentiment memory ({sentiment}, {source})")
            return True

        except Exception as exc:
            logger.exception(f"Supermemory REST error (add): {exc}")
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

        payload = {"q": query}

        try:
            resp = requests.post(
                f"{_BASE_URL}/search",
                json=payload,
                headers=self._headers(),
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()

            data = resp.json()

            # depending on API shape
            results = data.get("results", data)

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
            logger.exception(f"Supermemory REST error (search): {exc}")
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