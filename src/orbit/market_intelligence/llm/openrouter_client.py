import logging
import os
import requests
from typing import Optional

logger = logging.getLogger("Orbit")

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "qwen/qwen3-235b-a22b:free"
SITE_URL = os.getenv("OPENROUTER_SITE_URL", "https://orbit.local")
SITE_NAME = os.getenv("OPENROUTER_SITE_NAME", "Orbit")


class OpenRouterClient:
    """Thin HTTP wrapper around the OpenRouter chat-completions endpoint.

    Mimics the minimal interface used by the rest of the codebase:
        client.invoke(prompt: str) -> str | None
    """

    def __init__(
        self,
        api_key: str,
        model: str = OPENROUTER_MODEL,
        timeout: int = 60,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": SITE_URL,
            "X-OpenRouter-Title": SITE_NAME,
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def invoke(self, prompt: str) -> Optional[str]:
        """Send *prompt* to OpenRouter and return the assistant reply text."""
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }

        try:
            response = requests.post(
                OPENROUTER_API_URL,
                headers=self._headers,
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            return content
        except requests.HTTPError as http_err:
            logger.error(
                f"OpenRouter HTTP error {http_err.response.status_code}: "
                f"{http_err.response.text}"
            )
            raise
        except (KeyError, IndexError) as parse_err:
            logger.error(f"OpenRouter response parse error: {parse_err}")
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
