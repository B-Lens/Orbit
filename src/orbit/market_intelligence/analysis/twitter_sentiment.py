"""
twitter_sentiment
=================

LLM-based sentiment analysis for a batch of financial tweets.

The analyser scores each tweet individually (BULLISH / BEARISH / NEUTRAL)
and then aggregates them into a single :class:`TwitterSentimentResult` using
engagement-weighted voting.
"""

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from orbit.market_intelligence.analysis.reddit_sentiment import extract_json
from orbit.market_intelligence.llm.llm_endpoint import LLM

logger = logging.getLogger("Orbit")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class TweetSentimentEntry(BaseModel):
    """Sentiment for a single tweet."""

    tweet_id: str
    text: str
    sentiment: str  # BULLISH | BEARISH | NEUTRAL
    confidence: float = Field(ge=0.0, le=1.0)
    engagement_score: float = 0.0
    weight: float = 1.0


class TwitterSentimentResult(BaseModel):
    """Aggregated sentiment across all analysed tweets."""

    sentiment: str  # BULLISH | BEARISH | NEUTRAL
    confidence: float = Field(ge=0.0, le=1.0)
    overall_score: float  # -1.0 (bearish) … +1.0 (bullish)
    total_tweets_analyzed: int
    explanation: str


# ---------------------------------------------------------------------------
# Analyser
# ---------------------------------------------------------------------------


class TwitterSentimentAnalyzer:
    """Analyse financial tweets with an LLM and aggregate results.

    Args:
        llm: LLM wrapper that exposes ``invoke(prompt: str) -> str | None``.
        batch_size: Number of tweets sent to the LLM in a single prompt.
    """

    def __init__(self, llm: LLM, batch_size: int = 30) -> None:
        self.llm = llm
        self.batch_size = batch_size

    # ------------------------------------------------------------------
    # BATCH ANALYSIS
    # ------------------------------------------------------------------

    def analyze_tweets(
        self, tweets: List[Dict[str, Any]]
    ) -> List[TweetSentimentEntry]:
        """
        Analyse tweets in batches and return per-tweet sentiment entries.

        Args:
            tweets: Enriched tweet dicts (must contain ``id``, ``text``,
                    ``_weight``, ``_engagement_score``).

        Returns:
            List of :class:`TweetSentimentEntry` objects.
        """
        entries: List[TweetSentimentEntry] = []

        for i in range(0, len(tweets), self.batch_size):
            batch = tweets[i : i + self.batch_size]
            batch_entries = self._analyze_batch(batch)
            entries.extend(batch_entries)

        return entries

    def _analyze_batch(
        self, tweets: List[Dict[str, Any]]
    ) -> List[TweetSentimentEntry]:
        """
        Send one batch of tweets to the LLM and parse the response.

        Args:
            tweets: Subset of enriched tweet dicts.

        Returns:
            List of :class:`TweetSentimentEntry` objects for this batch.
        """
        if not tweets:
            return []

        # Build numbered tweet list for the prompt
        tweet_lines: List[str] = []
        for idx, t in enumerate(tweets, start=1):
            text = t.get("text", "").replace("\n", " ").strip()
            tweet_lines.append(f"{idx}. {text}")

        tweets_block = "\n".join(tweet_lines)

        prompt = f"""
            You are an institutional financial sentiment classifier.

            Task:
            Classify the market sentiment of EACH tweet with respect to tradable assets
            (crypto, stocks, gold, forex, macro, interest rates, risk sentiment).

            Ignore:
            - opinions
            - technical analysis
            - minor commentary
            - speculation
            - duplicate news

            Rules:
            - BULLISH → positive for risk assets / price likely up
            - BEARISH → negative for risk assets / price likely down
            - NEUTRAL → informational, unclear, mixed, or no market impact
            - Ignore jokes, memes, emojis unless they imply direction
            - If tweet is not market-related → NEUTRAL with low confidence (<=0.4)
            - If mixed signals → NEUTRAL
            - High conviction language → higher confidence
            - Speculation words ("maybe", "could") → reduce confidence
            - News headlines without opinion → NEUTRAL unless clearly directional

            Confidence Guidelines:
            0.9-1.0  : explicit strong directional claim
            0.7-0.89 : clear directional bias
            0.4-0.69 : weak / implied direction
            0.0-0.39 : unclear / irrelevant

            Output format:
            Return ONLY a valid JSON array. No markdown. No explanation.

            Schema:
            [
            {{
                "index": int,
                "sentiment": "BULLISH" | "BEARISH" | "NEUTRAL",
                "confidence": float
            }}
            ]

            Tweets:
            {tweets_block}
        """

        try:
            raw = self.llm.invoke(prompt)
            raw = str(raw).strip()
            logger.info(f"LLM raw output for Twitter sentiment batch: {raw}")
            # parsed = extract_json(raw)
            parsed = raw # For debugging: use raw output if parsing fails

            # extract_json returns a dict; if the LLM returned a list it will
            # be wrapped — handle both cases.
            if isinstance(parsed, list):
                items = parsed
            elif isinstance(parsed, dict):
                # Try common wrapper keys
                items = parsed.get("results") or parsed.get("data") or []
            else:
                items = []

        except Exception as exc:
            logger.exception(f"Twitter batch LLM call failed: {exc}")
            items = []

        entries: List[TweetSentimentEntry] = []
        for item in items:
            try:
                idx = int(item.get("index", 0)) - 1
                if idx < 0 or idx >= len(tweets):
                    continue
                tweet = tweets[idx]
                entries.append(
                    TweetSentimentEntry(
                        tweet_id=str(tweet.get("id", "")),
                        text=tweet.get("text", ""),
                        sentiment=str(item.get("sentiment", "NEUTRAL")).upper(),
                        confidence=float(item.get("confidence", 0.5)),
                        engagement_score=float(
                            tweet.get("_engagement_score", 0.0)
                        ),
                        weight=float(tweet.get("_weight", 1.0)),
                    )
                )
            except Exception as exc:
                logger.debug(f"Skipping malformed tweet sentiment item: {exc}")

        # Fallback: if LLM returned nothing, mark all as NEUTRAL
        if not entries:
            for tweet in tweets:
                entries.append(
                    TweetSentimentEntry(
                        tweet_id=str(tweet.get("id", "")),
                        text=tweet.get("text", ""),
                        sentiment="NEUTRAL",
                        confidence=0.3,
                        engagement_score=float(
                            tweet.get("_engagement_score", 0.0)
                        ),
                        weight=float(tweet.get("_weight", 1.0)),
                    )
                )

        return entries

    # ------------------------------------------------------------------
    # AGGREGATION
    # ------------------------------------------------------------------

    def aggregate(
        self, entries: List[TweetSentimentEntry]
    ) -> TwitterSentimentResult:
        """
        Aggregate per-tweet sentiment into a single result using
        engagement-weighted voting.

        Each tweet's vote is:
            direction * confidence * engagement_weight * query_weight

        where ``direction`` is +1 (BULLISH), -1 (BEARISH), or 0 (NEUTRAL)
        and ``engagement_weight`` is a normalised engagement score.

        Args:
            entries: Per-tweet sentiment entries.

        Returns:
            :class:`TwitterSentimentResult`.
        """
        if not entries:
            return TwitterSentimentResult(
                sentiment="NEUTRAL",
                confidence=0.3,
                overall_score=0.0,
                total_tweets_analyzed=0,
                explanation="No tweets available for analysis.",
            )

        # Normalise engagement scores to [0.1, 1.0] so low-engagement tweets
        # still contribute a little.
        max_eng = max(e.engagement_score for e in entries) or 1.0
        min_eng = min(e.engagement_score for e in entries)
        eng_range = max_eng - min_eng or 1.0

        direction_map = {"BULLISH": 1.0, "BEARISH": -1.0, "NEUTRAL": 0.0}

        weighted_score = 0.0
        total_weight = 0.0

        for entry in entries:
            norm_eng = 0.1 + 0.9 * (entry.engagement_score - min_eng) / eng_range
            vote_weight = norm_eng * entry.weight * entry.confidence
            direction = direction_map.get(entry.sentiment, 0.0)
            weighted_score += direction * vote_weight
            total_weight += vote_weight

        overall_score = weighted_score / total_weight if total_weight > 0 else 0.0
        overall_score = max(-1.0, min(1.0, overall_score))

        if overall_score > 0.15:
            label = "BULLISH"
        elif overall_score < -0.15:
            label = "BEARISH"
        else:
            label = "NEUTRAL"

        # Confidence = average per-tweet confidence weighted by engagement
        avg_confidence = (
            sum(e.confidence * e.engagement_score for e in entries)
            / sum(e.engagement_score or 1.0 for e in entries)
        )
        avg_confidence = round(min(1.0, max(0.0, avg_confidence)), 3)

        bullish_count = sum(1 for e in entries if e.sentiment == "BULLISH")
        bearish_count = sum(1 for e in entries if e.sentiment == "BEARISH")
        neutral_count = sum(1 for e in entries if e.sentiment == "NEUTRAL")

        explanation = (
            f"Analyzed {len(entries)} tweets: "
            f"{bullish_count} BULLISH, {bearish_count} BEARISH, {neutral_count} NEUTRAL. "
            f"Engagement-weighted score: {overall_score:.3f}."
        )

        return TwitterSentimentResult(
            sentiment=label,
            confidence=avg_confidence,
            overall_score=round(overall_score, 4),
            total_tweets_analyzed=len(entries),
            explanation=explanation,
        )
