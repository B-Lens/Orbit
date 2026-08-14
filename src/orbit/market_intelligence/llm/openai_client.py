"""OpenAI Responses API client for Orbit market intelligence."""

import logging
import os
from typing import Optional

from openai import OpenAI

logger = logging.getLogger("Orbit")

DEFAULT_OPENAI_MODEL = "gpt-5.6-terra"
DEFAULT_MAX_OUTPUT_TOKENS = 2_000
DEFAULT_INSTRUCTIONS = (
    "You are Orbit's market-intelligence analyst. Follow the requested output "
    "schema exactly. When JSON is requested, return only valid JSON without "
    "Markdown fences or additional commentary. Do not invent market data."
)


class OpenAIResponsesClient:
    """Small adapter exposing the ``invoke(prompt)`` interface used by Orbit."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 60.0,
        max_output_tokens: Optional[int] = None,
        client: Optional[OpenAI] = None,
    ) -> None:
        resolved_api_key = api_key or os.getenv("OPENAI_API_KEY")
        if client is None and not resolved_api_key:
            raise ValueError("OPENAI_API_KEY is required for the OpenAI provider")

        self.model = model or os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
        self.max_output_tokens = max_output_tokens or int(
            os.getenv("OPENAI_MAX_OUTPUT_TOKENS", str(DEFAULT_MAX_OUTPUT_TOKENS))
        )
        if self.max_output_tokens < 1:
            raise ValueError("OPENAI_MAX_OUTPUT_TOKENS must be positive")
        self.client = client or OpenAI(api_key=resolved_api_key, timeout=timeout)

    def invoke(self, prompt: str) -> str:
        """Generate a market-intelligence response through the Responses API."""
        if not prompt or not prompt.strip():
            raise ValueError("prompt must not be empty")

        response = self.client.responses.create(
            model=self.model,
            instructions=DEFAULT_INSTRUCTIONS,
            input=prompt,
            max_output_tokens=self.max_output_tokens,
        )
        output_text = response.output_text
        if not output_text or not output_text.strip():
            raise RuntimeError("OpenAI returned an empty response")

        logger.info("OpenAI market-intelligence response generated with %s", self.model)
        return output_text.strip()
