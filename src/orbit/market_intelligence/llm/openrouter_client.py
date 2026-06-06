import os
import logging
from typing import Optional
from openai import OpenAI

logger = logging.getLogger("Orbit")

OPENROUTER_MODEL = "openrouter/free"
RETRY_MODEL = [
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning-20260428:free",
    "liquid/lfm-2.5-1.2b-thinking-20260120:free",
    "nvidia/nemotron-3.5-content-safety-20260604:free",
]
MAX_RETRIES = 3

class OpenRouterClient:
    """Thin wrapper around the OpenRouter chat-completions endpoint via the OpenAI SDK.

    Mimics the minimal interface used by the rest of the codebase:
        client.invoke(prompt: str) -> str | None

    If the response is routed to a model in ``RETRY_MODEL`` (including known
    content-safety models that return non‑actionable text), the client will
    re‑call the LLM up to ``MAX_RETRIES`` times hoping to be routed to a
    different model.  If after the retries the model is still one of the
    undesirable ones, the response is discarded and ``None`` is returned.
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
            api_key=self.api_key,
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def invoke(self, prompt: str) -> Optional[str]:
        """Send a prompt and return text content, with retry logic for unwanted models."""
        # We start with attempt 0 and go up to MAX_RETRIES (the final fallback).
        for attempt in range(0, MAX_RETRIES + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                )
            except Exception as e:
                logger.info(f"LLM Inference failed on attempt {attempt}")
                if attempt == MAX_RETRIES:
                    return None
                continue

            # Determine the actual model used for this request
            routed_model = getattr(response, "model", None)
            logger.info("OpenRouter routed to model: %s (attempt %d)", routed_model, attempt)

            # If the routed model is one we want to avoid, initiate a retry
            # unless we are already on the final allowed attempt.
            if routed_model in RETRY_MODEL and attempt < MAX_RETRIES + 1:
                logger.info(
                    "Response from %s – retrying (%d/%d)",
                    RETRY_MODEL,
                    attempt,
                    MAX_RETRIES,
                )
                continue

            if routed_model in RETRY_MODEL and attempt == MAX_RETRIES:
                logger.warning(
                    "Final attempt also routed to %s – skipping this response",
                    RETRY_MODEL,
                )

            extracted_response = self._extract_content(response)
            if extracted_response is None:
                logger.warning("Extracted Response is None")
                continue
            return extracted_response

        # Should never be reached, but guard
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_content(self, response) -> Optional[str]:
        """Safely extract text content from an OpenAI chat response."""
        try:
            choices = response.choices
            if not choices:
                return None
            msg = choices[0].message
            if msg is None:
                return None

            # Handle text
            if isinstance(msg.content, str) and msg.content.strip():
                return msg.content

            # Handle structured content
            if isinstance(msg.content, list):
                logger.warning("Received List message instead of raw text")
                text = "".join(
                    part.get("text", "") for part in msg.content if part.get("type") == "text"
                )
                if text.strip():
                    return text

            # Handle tool calls
            if getattr(msg, "tool_calls", None):
                logger.warning("Received tool call instead of text")
                return None

            logger.warning("Empty or unsupported content in response")
            return None
        except (AttributeError, IndexError, TypeError) as e:
            logger.exception("Failed to extract content: %s", e)
            return None

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
