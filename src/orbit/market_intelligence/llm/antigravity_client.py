"""Google Antigravity Interactions API client."""

import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable, Optional


DEFAULT_ANTIGRAVITY_AGENT = "antigravity-preview-05-2026"
DEFAULT_ANTIGRAVITY_URL = (
    "https://generativelanguage.googleapis.com/v1beta/interactions"
)


class AntigravityClient:
    """Adapter for Google's managed Antigravity agent.

    Antigravity includes Google Search and URL retrieval, so it can satisfy both
    Orbit's ordinary inference and live-web provider contracts.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        agent: Optional[str] = None,
        timeout: float = 300.0,
        endpoint: Optional[str] = None,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self.api_key = api_key or os.getenv("ANTIGRAVITY_API_KEY") or os.getenv(
            "GEMINI_API_KEY"
        )
        if not self.api_key:
            raise ValueError("ANTIGRAVITY_API_KEY or GEMINI_API_KEY is required")
        self.agent = agent or os.getenv(
            "ANTIGRAVITY_AGENT", DEFAULT_ANTIGRAVITY_AGENT
        )
        self.timeout = timeout
        self.endpoint = endpoint or os.getenv(
            "ANTIGRAVITY_API_URL", DEFAULT_ANTIGRAVITY_URL
        )
        self._urlopen = urlopen

    def invoke(self, prompt: str) -> str:
        return self._invoke(prompt)

    def invoke_web_search(self, prompt: str) -> str:
        return self._invoke(prompt)

    def _invoke(self, prompt: str) -> str:
        if not prompt or not prompt.strip():
            raise ValueError("prompt must not be empty")

        payload = json.dumps(
            {
                "agent": self.agent,
                "input": prompt,
                "environment": "remote",
                "tools": [
                    {"type": "google_search"},
                    {"type": "url_context"},
                ],
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
            },
            method="POST",
        )
        try:
            with self._urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Antigravity request failed with HTTP {error.code}: {details}"
            ) from error
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Antigravity request failed: {error}") from error

        if not isinstance(data, dict):
            raise RuntimeError("Antigravity returned an invalid response")
        status = data.get("status")
        if status != "completed":
            raise RuntimeError(
                f"Antigravity interaction ended with status {status!r}"
            )

        output = ""
        steps = data.get("steps")
        if isinstance(steps, list):
            for step in reversed(steps):
                if not isinstance(step, dict) or step.get("type") != "model_output":
                    continue
                content = step.get("content")
                if isinstance(content, list):
                    output = "".join(
                        part.get("text", "")
                        for part in content
                        if isinstance(part, dict)
                        and part.get("type") == "text"
                        and isinstance(part.get("text"), str)
                    )
                break
        if not output.strip():
            raise RuntimeError("Antigravity returned an empty response")
        return output.strip()
