"""Live web-grounded market sentiment workflow."""

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from orbit.core.exception_manager import ExceptionManager
from orbit.llm.llm_endpoint import LLM
from orbit.llm.prompt_manager import PromptManager
from orbit.market_intelligence.models.mongodb_models import (
    MongoDBManager,
    SentimentRecord,
)
from orbit.market_intelligence.utils.utils import SentimentType
from orbit.utils.utils import extract_json, get_indian_time


class Sentiment(BaseModel):
    """Validated sentiment returned by a market-intelligence provider."""

    sentiment: SentimentType
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str


class WebSearchSentiment(Sentiment):
    """Validated sentiment grounded in live web sources."""

    sources: List[str] = Field(min_length=1, max_length=8)

    @field_validator("sources")
    @classmethod
    def validate_sources(cls, sources: List[str]) -> List[str]:
        unique_sources = list(dict.fromkeys(sources))
        if any(
            not source.startswith(("https://", "http://")) for source in unique_sources
        ):
            raise ValueError("web-search sources must be HTTP URLs")
        return unique_sources


class SentimentWorkflow(ExceptionManager):
    """Generate and persist the half-hourly live-web market assessment."""

    def __init__(self, llm: LLM) -> None:
        self.llm = llm
        # Connect lazily inside the guarded analysis cycle.  MongoDB may be
        # temporarily unavailable while Orbit starts, and that must not abort
        # the entire service before the scheduler has a chance to retry.
        self.mongodb: Optional[MongoDBManager] = None
        self.prompt_manager = PromptManager()

    def _get_mongodb(self) -> MongoDBManager:
        if self.mongodb is None:
            self.mongodb = MongoDBManager()
        return self.mongodb

    async def run_web_search_analysis(self) -> Dict[str, Any]:
        """Run a sourced market assessment and identify its provider."""
        start_time = time.time()
        try:
            prompt = self.prompt_manager.get_prompt(
                "global_crypto_web_sentiment_v3",
                current_time_utc=datetime.now(timezone.utc).isoformat(),
            )
            invocation = self.llm.invoke_web_search_with_provider(prompt)
            result = WebSearchSentiment(**extract_json(invocation.content))
            processing_time = int((time.time() - start_time) * 1000)
            provider = invocation.provider

            record = SentimentRecord(
                combined_sentiment={
                    "sentiment": result.sentiment,
                    "confidence": result.confidence,
                    "explanation": result.explanation,
                    "provider": provider,
                },
                reddit_sentiment={"source": "removed"},
                news_sentiment={
                    "source": "live_web_search",
                    "provider": provider,
                    "sources": result.sources,
                    "summary": result.explanation,
                },
                market_indicators={},
                twitter_sentiment={"source": "removed"},
                processing_time_ms=processing_time,
            )
            record_id = self._get_mongodb().save_sentiment(record)
            return {
                "success": True,
                "timestamp": get_indian_time().isoformat(),
                "database_id": record_id,
                "source": "live_web_search",
                "provider": provider,
                **result.model_dump(),
                "processing_time_ms": processing_time,
            }
        except Exception as error:
            self.handle_exception(error, context_description="run_web_search_analysis")
            return {
                "success": False,
                "error": str(error),
                "timestamp": get_indian_time().isoformat(),
                "source": "live_web_search",
            }
