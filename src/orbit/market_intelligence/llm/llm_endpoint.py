# Initialize LLM with fallback

import logging
import os
from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_groq import ChatGroq
import redis
from datetime import datetime
from dotenv import load_dotenv

from orbit.utils.utils import require_env
from orbit.core.exception_manager import ExceptionManager
from orbit.market_intelligence.llm.openrouter_client import OpenRouterClient

load_dotenv()

# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------
OPENROUTER_MODEL = "qwen/qwen3.6-plus:free"
GROQ_MODELS = ["openai/gpt-oss-120b", "llama-3.1-8b-instant", "gemma2-9b-it"]

logger = logging.getLogger("Orbit")


class LLM(ExceptionManager):
    """LLM wrapper with Groq as primary and OpenRouter as fallback."""

    def __init__(self) -> None:
        super().__init__()

        self.redis_client = redis.StrictRedis(
            host="localhost", port=6379, db=0, decode_responses=True
        )

        self.groq_llm = None
        self.openrouter_llm = None

        # ------------------------------------------------------------------
        # 1. Initialize Groq (PRIMARY)
        # ------------------------------------------------------------------
        groq_key = os.getenv("GROQ_API_KEY")
        if groq_key:
            os.environ["GROQ_API_KEY"] = groq_key

            for model_name in GROQ_MODELS:
                try:
                    logger.info(f"Trying Groq model: {model_name}")
                    candidate = ChatGroq(
                        model=model_name,
                        temperature=0,
                        timeout=30,
                    )
                    candidate.invoke("Test")
                    logger.info(f"Groq model '{model_name}' initialized successfully")
                    self.groq_llm = candidate
                    break
                except Exception as e:
                    logger.error(f"Failed Groq model '{model_name}': {e}")
        else:
            logger.warning("GROQ_API_KEY not set")

        # ------------------------------------------------------------------
        # 2. Initialize OpenRouter (FALLBACK)
        # ------------------------------------------------------------------
        openrouter_key = os.getenv("OPENROUTER_API_KEY")
        if openrouter_key:
            try:
                client = OpenRouterClient(
                    api_key=openrouter_key,
                    model=OPENROUTER_MODEL,
                )

                client.invoke("Test")

                logger.info(
                    f"OpenRouter model '{OPENROUTER_MODEL}' initialized successfully"
                )

                self.openrouter_llm = client

            except Exception as e:
                logger.error(f"OpenRouter initialization failed: {e}")
        else:
            logger.warning("OPENROUTER_API_KEY not set")

        # ------------------------------------------------------------------
        # 3. fail if both missing
        # ------------------------------------------------------------------
        if not self.groq_llm and not self.openrouter_llm:
            self.handle_exception(
                Exception("No LLM backend available"),
                context_description="LLM init failure",
            )

    # -----------------------------------------------------------------------
    # invoke
    # -----------------------------------------------------------------------

    def invoke(self, prompt: str) -> Optional[str]:
        if not self.groq_llm and not self.openrouter_llm:
            logger.error("No LLM available")
            return None

        prompt_token_length = len(prompt.split())
        logger.info(f"Invoking LLM token length: {prompt_token_length}")
        self._track_token_usage(prompt_token_length)

        # ----------------------------------------------------------
        # 1. Try GROQ first
        # ----------------------------------------------------------
        if self.groq_llm:
            try:
                response = self.groq_llm.invoke(prompt)
                return response.content
            except Exception as e:
                logger.error(f"Groq failed, falling back to OpenRouter: {e}")

        # ----------------------------------------------------------
        # 2. Fallback → OpenRouter
        # ----------------------------------------------------------
        try:
            return self.openrouter_llm.invoke(prompt)
        except Exception as e:
            logger.error(f"OpenRouter fallback failed: {e}")
            self.handle_exception(
                e,
                context_description="LLM fallback failure",
            )

        return None

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