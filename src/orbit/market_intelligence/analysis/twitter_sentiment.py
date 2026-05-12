"""
twitter_sentiment
=================

LLM-based sentiment analysis for a batch of financial tweets.

The analyser sends all tweets to the LLM in chunks (respecting token limits),
collects per-chunk overall sentiments, then produces a single final
:class:`TwitterSentimentResult` by asking the LLM to reason across all
chunk summaries.
"""

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from orbit.utils.utils import extract_json
from orbit.market_intelligence.llm.llm_endpoint import LLM

logger = logging.getLogger("Orbit")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class ChunkSentimentSummary(BaseModel):
    """Intermediate sentiment summary for one chunk of tweets."""

    sentiment: str  # BULLISH | BEARISH | NEUTRAL
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


class TwitterSentimentResult(BaseModel):
    """Final aggregated sentiment across all analysed tweets."""

    sentiment: str  # BULLISH | BEARISH | NEUTRAL
    confidence: float = Field(ge=0.0, le=1.0)
    overall_score: float  # -1.0 (bearish) … +1.0 (bullish)
    total_tweets_analyzed: int
    explanation: str


# ---------------------------------------------------------------------------
# Analyser
# ---------------------------------------------------------------------------

# Maximum number of tweets per LLM chunk call.
# Keeps each prompt well within typical 4 k-token context windows.
_CHUNK_SIZE = 40

class TwitterSentimentAnalyzer:
    """Analyse financial tweets with an LLM using a chunk-then-synthesise approach.

    Instead of scoring tweets individually and aggregating scores, this
    analyser:

    1. Splits tweets into chunks of ``chunk_size``.
    2. Asks the LLM for an **overall** sentiment + reasoning for each chunk.
    3. Feeds all chunk summaries back to the LLM for a **final synthesis**
       that produces one :class:`TwitterSentimentResult`.

    Args:
        llm: LLM wrapper that exposes ``invoke(prompt: str) -> str | None``.
        chunk_size: Number of tweets sent to the LLM in a single chunk prompt.
    """

    def __init__(self, llm: LLM, chunk_size: int = _CHUNK_SIZE) -> None:
        self.llm = llm
        self.chunk_size = chunk_size

    # ------------------------------------------------------------------
    # PUBLIC ENTRY POINT
    # ------------------------------------------------------------------

    def analyze_tweets(
        self, tweets: List[Dict[str, Any]]
    ) -> List[ChunkSentimentSummary]:
        """
        Analyse all tweets in chunks and return per-chunk summaries.

        Args:
            tweets: Enriched tweet dicts (must contain at least ``text``).

        Returns:
            List of :class:`ChunkSentimentSummary` objects — one per chunk.
        """
        summaries: List[ChunkSentimentSummary] = []

        for i in range(0, len(tweets), self.chunk_size):
            chunk = tweets[i : i + self.chunk_size]
            summary = self._analyze_chunk(chunk, chunk_index=i // self.chunk_size + 1)
            summaries.append(summary)

        return summaries

    # ------------------------------------------------------------------
    # CHUNK ANALYSIS
    # ------------------------------------------------------------------

    def _analyze_chunk(
        self, tweets: List[Dict[str, Any]], chunk_index: int = 1
    ) -> ChunkSentimentSummary:
        """
        Send one chunk of tweets to the LLM and return an overall sentiment
        summary for that chunk.

        Args:
            tweets: Subset of enriched tweet dicts.
            chunk_index: 1-based chunk number (used for logging only).

        Returns:
            :class:`ChunkSentimentSummary` for this chunk.
        """
        if not tweets:
            return ChunkSentimentSummary(
                sentiment="NEUTRAL",
                confidence=0.3,
                reasoning="Empty chunk.",
            )

        tweet_lines: List[str] = []
        for idx, t in enumerate(tweets, start=1):
            text = t.get("text", "").replace("\n", " ").strip()
            tweet_lines.append(f"{idx}. {text}")

        tweets_block = "\n".join(tweet_lines)
        logger.info(
            f"Analyzing tweet chunk {chunk_index} ({len(tweets)} tweets) with LLM"
        )

        prompt = f"""
            You are an institutional financial sentiment analyst.

            Below is a batch of financial tweets. Read ALL of them and determine the
            OVERALL market sentiment they collectively express with respect to tradable
            assets (crypto, stocks, gold, forex, macro, interest rates, risk sentiment).

            Rules:
            - BULLISH  → net positive for risk assets / prices likely up
            - BEARISH  → net negative for risk assets / prices likely down
            - NEUTRAL  → mixed, unclear, or no meaningful market signal
            - Base your judgment on the WEIGHT OF EVIDENCE across all tweets, not on
            any single tweet.
            - Ignore jokes, memes, and off-topic content.
            - High-conviction directional language raises confidence.
            - Speculation words ("maybe", "could") lower confidence.

            Confidence guidelines:
            0.9-1.0  : strong, consistent directional signal across most tweets
            0.7-0.89 : clear directional bias in the majority of tweets
            0.4-0.69 : weak or mixed directional signal
            0.0-0.39 : mostly noise / irrelevant

            Tweets:
            {tweets_block}
            """

        RETURN_FORMAT = """
            Respond ONLY in valid JSON.

            Rules:
            - sentiment MUST be exactly one of: "BULLISH", "BEARISH", "NEUTRAL"
            - Give the confidence about the sentiment <0.0 - 1.0> (0.0 if completely unrelated to financial markets)
            - Provide the explanation in a field called "explanation" (concise synthesis of key themes).

            Respond in Json Format:
            {{
                "sentiment": "BULLISH",
                "confidence": 0.0,
                "explanation": "brief explanation"
            }}
            """

        prompt = prompt + "\n" + RETURN_FORMAT

        try:
            raw = self.llm.invoke(prompt)
            raw = str(raw).strip()
            logger.info(
                f"LLM raw output for tweet chunk {chunk_index}: {raw}"
            )
            data = extract_json(raw)

            if not data or "sentiment" not in data:
                logger.warning(
                    "Tweet chunk %d LLM output did not contain a valid sentiment key. Raw: %.200s",
                    chunk_index,
                    raw,
                )
                return ChunkSentimentSummary(
                    sentiment="NEUTRAL",
                    confidence=0.0,
                    reasoning="LLM output could not be parsed into a sentiment.",
                )

            sentiment = str(data.get("sentiment", "NEUTRAL")).upper()
            confidence = float(data.get("confidence", 0.5))
            reasoning = str(data.get("reasoning") or data.get("explanation", ""))

            return ChunkSentimentSummary(
                sentiment=sentiment,
                confidence=confidence,
                reasoning=reasoning,
            )
        except Exception as exc:
            logger.exception(
                f"Tweet chunk {chunk_index} LLM call failed: {exc}"
            )
            return ChunkSentimentSummary(
                sentiment="NEUTRAL",
                confidence=0.3,
                reasoning="LLM call failed for this chunk.",
            )

    # ------------------------------------------------------------------
    # FINAL SYNTHESIS
    # ------------------------------------------------------------------

    def aggregate(
        self, summaries: List[ChunkSentimentSummary], total_tweets: int = 0
    ) -> TwitterSentimentResult:
        """
        Synthesise all chunk summaries into a single :class:`TwitterSentimentResult`.

        If there is only one chunk summary the result is derived directly from
        it without an extra LLM call.  For multiple chunks the LLM is asked to
        reason across all summaries and produce a final verdict.

        Args:
            summaries: Per-chunk sentiment summaries from :meth:`analyze_tweets`.
            total_tweets: Total number of tweets analysed (for the result field).

        Returns:
            :class:`TwitterSentimentResult`.
        """
        if not summaries:
            return TwitterSentimentResult(
                sentiment="NEUTRAL",
                confidence=0.3,
                overall_score=0.0,
                total_tweets_analyzed=total_tweets,
                explanation="No tweets available for analysis.",
            )

        if len(summaries) == 1:
            s = summaries[0]
            score = self._sentiment_to_score(s.sentiment, s.confidence)
            return TwitterSentimentResult(
                sentiment=s.sentiment,
                confidence=s.confidence,
                overall_score=round(score, 4),
                total_tweets_analyzed=total_tweets,
                explanation=s.reasoning,
            )

        # Build a summary-of-summaries block for the synthesis prompt
        summary_lines: List[str] = []
        for i, s in enumerate(summaries, start=1):
            summary_lines.append(
                f"Chunk {i}: sentiment={s.sentiment}, "
                f"confidence={s.confidence:.2f}, reasoning={s.reasoning}"
            )
        summaries_block = "\n".join(summary_lines)

        prompt = f"""
            You are an institutional financial sentiment analyst.

            Below are sentiment summaries produced from separate batches of financial
            tweets. Each summary represents the collective sentiment of one batch.

            Your task: synthesise ALL summaries into a single overall market sentiment.

            Rules:
            - focus on the strongest signals or valid analysis of the chunk.
            - Weigh each chunk by its confidence score.
            - BULLISH  → net positive for risk assets
            - BEARISH  → net negative for risk assets
            - NEUTRAL  → mixed or no clear signal
            - Provide a concise explanation that references the key themes.
            - Ignore failed analysis or No analysis of the chunk
            - Ignore irrelevant content in the chunk summaries.

            Respond ONLY with valid JSON (no markdown, no extra text):
            {{
            "sentiment": "BULLISH" | "BEARISH" | "NEUTRAL",
            "confidence": <float 0.0-1.0>,
            "reasoning": "<concise synthesis explanation>"
            }}

            Chunk summaries:
            {summaries_block}
            """

        try:
            raw = self.llm.invoke(prompt)
            raw = str(raw).strip()
            logger.info(f"LLM synthesis output: {raw}")
            data = extract_json(raw)

            if not data or "sentiment" not in data:
                logger.warning(
                    "Synthesis LLM output did not contain a valid sentiment key. Raw: %.200s",
                    raw,
                )
                return TwitterSentimentResult(
                    sentiment="NEUTRAL",
                    confidence=0.0,
                    overall_score=0.0,
                    total_tweets_analyzed=0,
                    explanation="Tweets Analysis failed during final synthesis step. Fallback applied.",
                )

            sentiment = str(data.get("sentiment", "NEUTRAL")).upper()
            confidence = float(data.get("confidence", 0.5))
            reasoning = str(data.get("reasoning") or data.get("explanation", ""))
            score = self._sentiment_to_score(sentiment, confidence)

            return TwitterSentimentResult(
                sentiment=sentiment,
                confidence=round(confidence, 3),
                overall_score=round(score, 4),
                total_tweets_analyzed=total_tweets,
                explanation=reasoning,
            )

        except Exception as exc:
            logger.exception(f"Twitter synthesis LLM call failed: {exc}")

            return TwitterSentimentResult(
                sentiment="NEUTRAL",
                confidence=0.0,
                overall_score=0.0,
                total_tweets_analyzed=0,
                explanation="Tweets Analysis failed during final synthesis step. Fallback applied.",
            )

    # ------------------------------------------------------------------
    # INTERNAL HELPERS
    # ------------------------------------------------------------------

    @staticmethod
    def _sentiment_to_score(sentiment: str, confidence: float) -> float:
        """Convert a sentiment label + confidence to a [-1, 1] score."""
        direction = {"BULLISH": 1.0, "BEARISH": -1.0, "NEUTRAL": 0.0}.get(
            sentiment.upper(), 0.0
        )
        return max(-1.0, min(1.0, direction * confidence))
