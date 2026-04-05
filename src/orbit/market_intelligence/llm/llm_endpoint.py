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

load_dotenv()  # Load environment variables from .env file

# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------
OPENROUTER_MODEL = "qwen/qwen3.6-plus:free"
GROQ_FALLBACK_MODELS = ["openai/gpt-oss-120b", "llama-3.1-8b-instant", "gemma2-9b-it"]

logger = logging.getLogger("Orbit")


class LLM(ExceptionManager):
    """LLM wrapper with OpenRouter as primary and Groq as fallback.

    Invocation interface::

        llm = LLM()
        text: str | None = llm.invoke("your prompt")
    """

    def __init__(self) -> None:
        super().__init__()
        self.redis_client = redis.StrictRedis(
            host="localhost", port=6379, db=0, decode_responses=True
        )
        self.llm = None          # holds the active backend
        self._use_openrouter = False  # flag so invoke() knows which path to take

        # ------------------------------------------------------------------
        # 1. Try OpenRouter (primary)
        # ------------------------------------------------------------------
        openrouter_key = os.getenv("OPENROUTER_API_KEY")
        if openrouter_key:
            try:
                client = OpenRouterClient(api_key=openrouter_key, model=OPENROUTER_MODEL)
                # Smoke-test the endpoint
                test_response = client.invoke("Test")
                logger.info(
                    f"OpenRouter model '{OPENROUTER_MODEL}' initialized successfully. "
                    f"Test reply: {test_response!r}"
                )
                self.llm = client
                self._use_openrouter = True
                return  # primary succeeded — skip fallback setup
            except Exception as e:
                logger.error(
                    f"OpenRouter initialization failed, falling back to Groq: {e}"
                )
        else:
            logger.warning(
                "OPENROUTER_API_KEY not set — skipping OpenRouter, trying Groq."
            )

        # ------------------------------------------------------------------
        # 2. Groq fallback
        # ------------------------------------------------------------------
        groq_key = os.getenv("GROQ_API_KEY")
        if groq_key:
            os.environ["GROQ_API_KEY"] = groq_key  # ensure LangChain picks it up
        else:
            logger.warning("GROQ_API_KEY not set — Groq fallback unavailable.")

        for model_name in GROQ_FALLBACK_MODELS:
            try:
                logger.info(f"Trying Groq model: {model_name}")
                candidate = ChatGroq(model=model_name, temperature=0, timeout=30)
                candidate.invoke("Test")
                logger.info(f"Groq model '{model_name}' initialized successfully.")
                self.llm = candidate
                self._use_openrouter = False
                return
            except Exception as e:
                logger.error(f"Failed to initialize Groq model '{model_name}': {e}")

        # ------------------------------------------------------------------
        # 3. All backends failed
        # ------------------------------------------------------------------
        logger.error("All LLM backends failed to initialize.")
        self.handle_exception(
            Exception("All LLM backends (OpenRouter + Groq) failed to initialize"),
            context_description="LLM initialization failure",
        )

    # -----------------------------------------------------------------------
    # Public invoke
    # -----------------------------------------------------------------------

    def invoke(self, prompt: str) -> Optional[str]:
        """Invoke the active LLM backend with *prompt* and return reply text."""
        if not self.llm:
            logger.error("LLM is not initialized — cannot invoke.")
            return None

        # Token-count monitoring
        prompt_token_length = len(prompt.split())
        logger.info(f"Invoking LLM with prompt token length: {prompt_token_length}")
        self._track_token_usage(prompt_token_length)

        try:
            if self._use_openrouter:
                # OpenRouterClient.invoke() returns a plain string
                return self.llm.invoke(prompt)
            else:
                # LangChain ChatGroq returns an object with .content
                response = self.llm.invoke(prompt)
                return response.content
        except Exception as e:
            logger.error(f"LLM invocation failed: {e}")
            self.handle_exception(e, context_description="LLM invocation failure")
            return None

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _track_token_usage(self, token_count: int) -> None:
        """Persist cumulative daily token usage in Redis."""
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
            logger.exception(f"Failed to update API token count in Redis: {e}")
            self.handle_exception(
                e,
                context_description="Failed to update API token count in Redis",
            )
