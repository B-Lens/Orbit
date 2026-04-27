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
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
            )

            msg = response.choices[0].message
            actual_model = getattr(response, "model", "unknown")
            logger.info(f"OpenRouter routed to model: {actual_model}")

            # Handle text
            if isinstance(msg.content, str) and msg.content.strip():
                return msg.content

            # Handle structured content
            if isinstance(msg.content, list):
                text = "".join(
                    part.get("text", "") for part in msg.content if part.get("type") == "text"
                )
                if text.strip():
                    return text

            # Handle tool calls
            if getattr(msg, "tool_calls", None):
                logger.warning("Received tool call instead of text")
                return None

            logger.warning(f"Empty response received: {response}")
            return None
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
