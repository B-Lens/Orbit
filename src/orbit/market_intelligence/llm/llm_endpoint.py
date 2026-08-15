"""Provider routing for Orbit market intelligence."""

import logging
import os
from typing import Any, Optional

from langchain_groq import ChatGroq
import redis
from datetime import datetime
from dotenv import load_dotenv

from orbit.core.exception_manager import ExceptionManager
from orbit.market_intelligence.llm.openai_client import (
    CodexOAuthResponsesClient,
    OpenAIResponsesClient,
    default_auth_file,
)
from orbit.market_intelligence.llm.openrouter_client import OpenRouterClient

load_dotenv()

# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-terra")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free")
GROQ_MODELS = ["openai/gpt-oss-120b", "llama-3.1-8b-instant", "gemma2-9b-it"]

logger = logging.getLogger("Orbit")

class LLM(ExceptionManager):
    """Use OpenAI first, with optional OpenRouter and Groq fallbacks."""

    def __init__(
        self,
        openai_client: Optional[Any] = None,
        openrouter_client: Optional[Any] = None,
        groq_client: Optional[Any] = None,
        redis_client: Optional[Any] = None,
    ) -> None:
        super().__init__()

        self.redis_client = redis_client or redis.StrictRedis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            db=int(os.getenv("REDIS_DB", "0")),
            decode_responses=True,
        )

        self.openai_llm = openai_client
        self.openrouter_llm = openrouter_client
        self.groq_llm = groq_client

        # ------------------------------------------------------------------
        # 1. OpenAI Responses API (PRIMARY)
        # ------------------------------------------------------------------
        if self.openai_llm is None and os.getenv("OPENAI_API_KEY"):
            self.openai_llm = OpenAIResponsesClient(model=OPENAI_MODEL)
            logger.info("OpenAI model '%s' configured as primary", OPENAI_MODEL)
        elif self.openai_llm is None and default_auth_file().is_file():
            self.openai_llm = CodexOAuthResponsesClient(model=OPENAI_MODEL)
            logger.info("OpenAI model '%s' configured with Codex OAuth", OPENAI_MODEL)

        # ------------------------------------------------------------------
        # 2. OpenRouter (FALLBACK)
        # ------------------------------------------------------------------
        if self.openrouter_llm is None and os.getenv("OPENROUTER_API_KEY"):
            self.openrouter_llm = OpenRouterClient(
                model=OPENROUTER_MODEL,
            )
            logger.info("OpenRouter model '%s' configured as fallback", OPENROUTER_MODEL)

        # ------------------------------------------------------------------
        # 3. Groq (LAST FALLBACK)
        # ------------------------------------------------------------------
        if self.groq_llm is None and os.getenv("GROQ_API_KEY"):
            for model_name in GROQ_MODELS:
                try:
                    self.groq_llm = ChatGroq(
                        model=model_name,
                        temperature=0,
                        timeout=30,
                    )
                    logger.info("Groq model '%s' configured as final fallback", model_name)
                    break
                except Exception as error:
                    logger.warning("Could not configure Groq model '%s': %s", model_name, error)

        if not any((self.openai_llm, self.openrouter_llm, self.groq_llm)):
            raise RuntimeError(
                "No market-intelligence LLM configured. Set OPENAI_API_KEY, "
                "provide OPENAI_AUTH_FILE, "
                "or configure an optional fallback provider."
            )

    def invoke(self, prompt: str) -> str:
        """Invoke providers in deterministic priority order."""
        prompt_token_length = len(prompt.split())
        logger.info("Invoking market-intelligence LLM with about %d words", prompt_token_length)
        self._track_token_usage(prompt_token_length)

        providers = (
            ("OpenAI", self.openai_llm),
            ("OpenRouter", self.openrouter_llm),
            ("Groq", self.groq_llm),
        )
        last_error: Optional[Exception] = None

        for provider_name, provider in providers:
            if provider is None:
                continue
            try:
                response = provider.invoke(prompt)
                content = response.content if hasattr(response, "content") else response
                if content and str(content).strip():
                    return str(content).strip()
                raise RuntimeError(f"{provider_name} returned an empty response")
            except Exception as error:
                last_error = error
                logger.exception("%s market-intelligence provider failed", provider_name)

        raise RuntimeError("All configured market-intelligence providers failed") from last_error

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _track_token_usage(self, token_count: int) -> None:
        try:
            today_str = datetime.now().strftime("%Y%m%d")
            redis_key = f"llm:api_token_count:{today_str}"

            if not self.redis_client.exists(redis_key):
                self.redis_client.set(redis_key, 0, ex=86400)

            self.redis_client.incrby(redis_key, token_count)

            if self.redis_client.ttl(redis_key) == -1:
                self.redis_client.expire(redis_key, 86400)

            cumulative = int(self.redis_client.get(redis_key) or 0)
            logger.info(f"Cumulative token count in 24 hrs: {cumulative}")

        except Exception as e:
            logger.exception(f"Failed token tracking: {e}")
            self.handle_exception(
                e,
                context_description="Token tracking failure",
            )
