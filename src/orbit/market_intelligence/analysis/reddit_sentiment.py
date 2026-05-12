# analysis/weighted_reddit_sentiment.py
import json
import logging
import re
from typing import List, Dict, Any, Optional, Literal
import numpy as np
from datetime import datetime
from pydantic import BaseModel, Field

from orbit.utils.utils import extract_json

logger = logging.getLogger("Orbit")

# Maximum characters per post snippet when building the combined prompt
_MAX_TITLE_CHARS = 200
_MAX_BODY_CHARS = 600
# Approximate token budget for all posts in a single LLM call.
# Each chunk will contain at most this many characters of post content.
_CHUNK_CHAR_LIMIT = 12_000


class RedditSentimentResult(BaseModel):
    sentiment: Literal["BULLISH", "BEARISH", "NEUTRAL"] = "NEUTRAL"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    explanation: str = "Failed to parse sentiment response"


class RedditOverallResult(BaseModel):
    """Final synthesised Reddit sentiment across all chunks."""
    sentiment: Literal["BULLISH", "BEARISH", "NEUTRAL"] = "NEUTRAL"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    explanation: str = "Failed to parse overall sentiment response"
    total_posts_analyzed: int
    chunks_analyzed: int


def _build_post_snippet(post: Dict[str, Any]) -> str:
    """Return a compact text representation of a single Reddit post."""
    title = (post.get("title") or "")[:_MAX_TITLE_CHARS]
    body = (post.get("body") or post.get("selftext") or "")[:_MAX_BODY_CHARS]
    subreddit = post.get("subreddit", "")
    parts = [f"[r/{subreddit}] {title}"]
    if body.strip():
        parts.append(body.strip())
    return "\n".join(parts)


def _chunk_posts(posts: List[Dict[str, Any]], char_limit: int = _CHUNK_CHAR_LIMIT) -> List[List[Dict[str, Any]]]:
    """
    Split posts into chunks so that the combined text of each chunk stays
    within *char_limit* characters.
    """
    chunks: List[List[Dict[str, Any]]] = []
    current_chunk: List[Dict[str, Any]] = []
    current_len = 0

    for post in posts:
        snippet = _build_post_snippet(post)
        if current_chunk and current_len + len(snippet) > char_limit:
            chunks.append(current_chunk)
            current_chunk = []
            current_len = 0
        current_chunk.append(post)
        current_len += len(snippet)

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


class WeightedRedditAnalyzer:
    """
    Analyse Reddit posts for market sentiment using a chunk-then-synthesise
    approach.

    1. Flattens all posts from all subreddits into a single list.
    2. Splits them into token-safe chunks.
    3. Asks the LLM for an **overall** sentiment + reasoning for each chunk.
    4. Feeds all chunk summaries back to the LLM for a **final synthesis**
       that produces one :class:`RedditOverallResult`.
    """

    def __init__(self, llm) -> None:
        self.llm = llm

    # ------------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------------

    def analyze(
        self,
        reddit_posts_data: Dict[str, Dict[str, Any]],
    ) -> RedditOverallResult:
        """
        Analyse all Reddit posts and return a single synthesised sentiment.

        Args:
            reddit_posts_data: Mapping of subreddit name → data dict with a
                               ``"posts"`` key containing a list of post dicts.

        Returns:
            :class:`RedditOverallResult` with final synthesised sentiment.
        """
        # Flatten all posts across subreddits
        all_posts: List[Dict[str, Any]] = []
        for data in reddit_posts_data.values():
            all_posts.extend(data.get("posts", []))

        if not all_posts:
            logger.warning("Reddit: no posts to analyse.")
            return RedditOverallResult(
                sentiment="NEUTRAL",
                confidence=0.0,
                explanation="No Reddit posts available.",
                total_posts_analyzed=0,
                chunks_analyzed=0,
            )

        chunks = _chunk_posts(all_posts)
        logger.info(
            f"Reddit: {len(all_posts)} posts split into {len(chunks)} chunk(s) for LLM analysis."
        )

        chunk_summaries: List[RedditSentimentResult] = []
        for idx, chunk in enumerate(chunks, start=1):
            summary = self._analyze_chunk(idx, chunk)
            chunk_summaries.append(summary)
            logger.info(
                f"Reddit chunk {idx}/{len(chunks)}: "
                f"{summary.sentiment} (conf={summary.confidence:.2f})"
            )

        if len(chunk_summaries) == 1:
            s = chunk_summaries[0]
            return RedditOverallResult(
                sentiment=s.sentiment,
                confidence=s.confidence,
                explanation=s.explanation,
                total_posts_analyzed=len(all_posts),
                chunks_analyzed=1,
            )

        return self._synthesise(chunk_summaries, total_posts=len(all_posts))

    # ------------------------------------------------------------------
    # CHUNK ANALYSIS
    # ------------------------------------------------------------------

    def _analyze_chunk(
        self,
        chunk_id: int,
        posts: List[Dict[str, Any]],
    ) -> RedditSentimentResult:
        """
        Ask the LLM for an overall sentiment for a single chunk of posts.

        Args:
            chunk_id: 1-based index used only for logging.
            posts: List of post dicts in this chunk.

        Returns:
            :class:`RedditSentimentResult` for this chunk.
        """
        snippets = "\n---\n".join(_build_post_snippet(p) for p in posts)

        prompt = f"""
            You are a financial sentiment analyst.

            Analyse the following Reddit posts (separated by ---) and determine the
            **overall** market/crypto sentiment expressed across ALL of them.

            Posts:
            {snippets}

            Rules:
            - sentiment MUST be exactly one of: "BULLISH", "BEARISH", "NEUTRAL"
            - confidence: float 0.0-1.0 reflecting how clearly the posts lean one way (set to 0.0 if completely unrelated to finanial markets)
            - explanation: concise synthesis of the key themes driving the sentiment
            - Focus on crypto / financial markets sentiment, not individual stocks

            Respond ONLY with valid JSON (no markdown, no extra text):
            {{
            "sentiment": "BULLISH" | "BEARISH" | "NEUTRAL",
            "confidence": <float 0.0-1.0>,
            "explanation": "<concise synthesis>"
            }}
            """

        try:
            raw = self.llm.invoke(prompt)
            logger.info(f"Reddit chunk {chunk_id} LLM raw output: {raw}")
            data = extract_json(str(raw))
            if data is None:
                logger.warning(f"Reddit chunk {chunk_id} LLM output could not be parsed as JSON.")
                return RedditSentimentResult()
            return RedditSentimentResult(**data)
        except Exception as e:
            logger.exception(f"Reddit chunk {chunk_id} LLM analysis failed: {e}")
            return RedditSentimentResult(
                sentiment="NEUTRAL",
                confidence=0.0,
                explanation="Chunk analysis failed — using neutral fallback.",
            )

    # ------------------------------------------------------------------
    # SYNTHESIS
    # ------------------------------------------------------------------

    def _synthesise(
        self,
        summaries: List[RedditSentimentResult],
        total_posts: int,
    ) -> RedditOverallResult:
        """
        Synthesise multiple chunk summaries into a single final sentiment.

        Args:
            summaries: One :class:`RedditSentimentResult` per chunk.
            total_posts: Total number of posts analysed across all chunks.

        Returns:
            :class:`RedditOverallResult` with synthesised sentiment.
        """
        summary_text = "\n".join(
            f"Chunk {i + 1}: sentiment={s.sentiment}, "
            f"confidence={s.confidence:.2f}, explanation={s.explanation}"
            for i, s in enumerate(summaries)
        )

        prompt = f"""
            You are a financial sentiment analyst.

            Below are sentiment summaries from {len(summaries)} batches of Reddit posts
            (covering {total_posts} posts in total).

            {summary_text}

            Synthesise these into a single overall Reddit market sentiment.

            Rules:
            - sentiment MUST be exactly one of: "BULLISH", "BEARISH", "NEUTRAL"
            - confidence: float 0.0–1.0
            - explanation: concise synthesis of the dominant themes

            Respond ONLY with valid JSON (no markdown, no extra text):
            {{
            "sentiment": "BULLISH" | "BEARISH" | "NEUTRAL",
            "confidence": <float 0.0-1.0>,
            "explanation": "<concise synthesis>"
            }}
            """

        try:
            raw = self.llm.invoke(prompt)
            logger.info(f"Reddit synthesis LLM raw output: {raw}")
            data = extract_json(str(raw))
            if data is None:
                return RedditOverallResult(sentiment="NEUTRAL", confidence=0.0, explanation="LLM output could not be parsed into overall sentiment.", total_posts_analyzed=total_posts, chunks_analyzed=len(summaries))
            return RedditOverallResult(
                **data,
                total_posts_analyzed=total_posts,
                chunks_analyzed=len(summaries),
            )
        except Exception as e:
            logger.exception(f"Reddit synthesis LLM call failed: {e}")
            # Fallback: simple majority vote
            counts = {"BULLISH": 0, "BEARISH": 0, "NEUTRAL": 0}
            total_conf = 0.0
            for s in summaries:
                counts[s.sentiment] = counts.get(s.sentiment, 0) + 1
                total_conf += s.confidence
            majority = max(counts, key=lambda k: counts[k])
            avg_conf = round(total_conf / len(summaries), 3)
            return RedditOverallResult(
                sentiment=majority,
                confidence=avg_conf,
                explanation="Synthesis failed — majority vote fallback used.",
                total_posts_analyzed=total_posts,
                chunks_analyzed=len(summaries),
            )
