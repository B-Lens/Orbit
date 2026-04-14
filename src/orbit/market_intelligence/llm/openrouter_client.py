import logging
import os
from typing import Optional
from openai import OpenAI

logger = logging.getLogger("Orbit")

OPENROUTER_MODEL = "openrouter/free"

class OpenRouterClient:
    """Thin wrapper around the OpenRouter chat-completions endpoint via the OpenAI SDK.

    Mimics the minimal interface used by the rest of the codebase:
        client.invoke(prompt: str) -> str | None
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = OPENROUTER_MODEL,
        timeout: int = 60,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY", "")
        self.model = model
        self.timeout = timeout
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=self.api_key
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def invoke(self, prompt: str) -> Optional[str]:
        """Send *prompt* to OpenRouter and return the assistant reply text."""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
            )
            actual_model = getattr(response, "model", "unknown")
            logger.info(f"OpenRouter routed to model: {actual_model}")
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"OpenRouter error: {e}")
            raise

    # ------------------------------------------------------------------
    # Compatibility shim so callers can treat this like a LangChain model
    # ------------------------------------------------------------------

    class _FakeContent:
        """Wraps a plain string so `.content` attribute access works."""

        def __init__(self, text: str) -> None:
            self.content = text

    def invoke_lc(self, prompt: str) -> "_FakeContent":
        """Return an object with a `.content` attribute (LangChain-style)."""
        text = self.invoke(prompt)
        return self._FakeContent(text or "")
