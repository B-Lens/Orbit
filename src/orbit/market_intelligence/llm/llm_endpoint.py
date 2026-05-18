# Initialize LLM with fallback

import logging
import os
from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_groq import ChatGroq
import redis
from datetime import datetime
from dotenv import load_dotenv
from langfuse.langchain import CallbackHandler

from orbit.utils.utils import require_env
from orbit.core.exception_manager import ExceptionManager
from orbit.market_intelligence.llm.openrouter_client import OpenRouterClient

load_dotenv()

# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------
OPENROUTER_MODEL = "openrouter/free"
GROQ_MODELS = ["openai/gpt-oss-120b", "llama-3.1-8b-instant", "gemma2-9b-it"]

logger = logging.getLogger("Orbit")

langfuse_handler = CallbackHandler()

class LLM(ExceptionManager):
    """LLM wrapper with Groq as primary and OpenRouter as fallback."""

    def __init__(self) -> None:
        super().__init__()

        self.redis_client = redis.StrictRedis(
            host="localhost", port=6379, db=0, decode_responses=True
        )

        self.groq_llm = None
        self.openrouter_llm = None

        os.environ["GROQ_API_KEY"] = os.getenv("GROQ_API_KEY")

        # ------------------------------------------------------------------
        # 1. Initialize OpenRouter (PRIMARY) 
        # ------------------------------------------------------------------
        try:
            client = OpenRouterClient(
                api_key=os.getenv("OPENROUTER_API_KEY"),
                model=OPENROUTER_MODEL,
            )

            client.invoke("Test")

            logger.info(
                f"OpenRouter model '{OPENROUTER_MODEL}' initialized successfully"
            )

            self.openrouter_llm = client

        except Exception as e:
            logger.exception(f"OpenRouter initialization failed")
            self.handle_exception(
                e,
                context_description="OpenRouter LLM init failure",
            )

        # ------------------------------------------------------------------
        # 2. Initialize Groq (FALLBACK)
        # ------------------------------------------------------------------
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

    
    # -----------------------------------------------------------------------
    # invoke
    # -----------------------------------------------------------------------

    def invoke(self, prompt: str, use_groq: bool = False) -> Optional[str]:

        assert self.groq_llm or self.openrouter_llm, "No LLM backend available"
    
        prompt_token_length = len(prompt.split())
        logger.info(f"Invoking LLM token length: {prompt_token_length}")
        self._track_token_usage(prompt_token_length)

        # ----------------------------------------------------------
        # 1. Use GROQ as primary only if explicitly requested
        # ----------------------------------------------------------
        if use_groq and self.groq_llm:
            try:
                response = self.groq_llm.invoke(
                            prompt,
                            config={
                            "callbacks": [langfuse_handler]
                            }
                        )
                return response.content if hasattr(response, "content") else response

            except Exception as e:
                logger.exception("Groq failed, falling back to OpenRouter")

                if self.openrouter_llm:
                    try:
                        return self.openrouter_llm.invoke(prompt)
                    except Exception as fallback_error:
                        self.handle_exception(
                            fallback_error,
                            context_description="OpenRouter fallback failure",
                        )

        # ----------------------------------------------------------
        # 1. Try OpenRouter first
        # ----------------------------------------------------------
        if self.openrouter_llm:
            try:
                return self.openrouter_llm.invoke(prompt)
            except Exception as e:
                logger.exception(f"OpenRouter failed, falling back to Groq")

        
        # ----------------------------------------------------------
        # 2. Fallback → GROQ
        # ----------------------------------------------------------
        if self.groq_llm:
            try:
                response = self.groq_llm.invoke(prompt)
                return response.content
            except Exception as e:
                self.handle_exception(
                    e,
                    context_description="Groq LLM fallback failure",
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