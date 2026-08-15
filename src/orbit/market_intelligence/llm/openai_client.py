"""OpenAI Responses API clients for Orbit market intelligence."""

import json
import logging
import os
from pathlib import Path
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

from openai import OpenAI

logger = logging.getLogger("Orbit")

DEFAULT_OPENAI_MODEL = "gpt-5.6-terra"
DEFAULT_MAX_OUTPUT_TOKENS = 2_000
DEFAULT_INSTRUCTIONS = (
    "You are Orbit's market-intelligence analyst. Follow the requested output "
    "schema exactly. When JSON is requested, return only valid JSON without "
    "Markdown fences or additional commentary. Do not invent market data."
)
DEFAULT_CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"


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
        if response.status != "completed":
            details = getattr(response, "incomplete_details", None)
            raise RuntimeError(
                f"OpenAI response ended with status {response.status!r}: {details}"
            )
        output_text = response.output_text
        if not output_text or not output_text.strip():
            raise RuntimeError("OpenAI returned an empty response")

        logger.info("OpenAI market-intelligence response generated with %s", self.model)
        return output_text.strip()


def default_auth_file() -> Path:
    """Return the Codex credential file location without requiring it to exist."""
    configured_file = os.getenv("OPENAI_AUTH_FILE")
    if configured_file:
        return Path(configured_file).expanduser()

    codex_home = os.getenv("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "auth.json"
    return Path.home() / ".codex" / "auth.json"


class CodexOAuthResponsesClient:
    """Responses adapter authenticated with a Codex CLI ``auth.json`` file.

    This path is useful in environments where the Codex login session is
    provisioned as a file instead of an API key. The file is re-read for every
    request so an externally refreshed login is picked up without a restart.
    """

    def __init__(
        self,
        auth_file: Optional[os.PathLike[str] | str] = None,
        model: Optional[str] = None,
        timeout: float = 60.0,
        max_output_tokens: Optional[int] = None,
        endpoint: Optional[str] = None,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self.auth_file = Path(auth_file).expanduser() if auth_file else default_auth_file()
        self.model = model or os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens or int(
            os.getenv("OPENAI_MAX_OUTPUT_TOKENS", str(DEFAULT_MAX_OUTPUT_TOKENS))
        )
        if self.max_output_tokens < 1:
            raise ValueError("OPENAI_MAX_OUTPUT_TOKENS must be positive")
        self.endpoint = endpoint or os.getenv(
            "OPENAI_CODEX_RESPONSES_URL", DEFAULT_CODEX_RESPONSES_URL
        )
        self._urlopen = urlopen

    def _credentials(self) -> tuple[str, Optional[str]]:
        try:
            auth = json.loads(self.auth_file.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise RuntimeError(f"Codex auth file not found: {self.auth_file}") from error
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Could not read Codex auth file: {self.auth_file}") from error

        auth = auth if isinstance(auth, dict) else {}
        tokens = auth.get("tokens")
        tokens = tokens if isinstance(tokens, dict) else {}
        access_token = tokens.get("access_token") or auth.get("access_token")
        account_id = tokens.get("account_id") or auth.get("account_id")
        if not isinstance(access_token, str) or not access_token:
            raise RuntimeError("Codex auth.json does not contain an access token")
        return access_token, account_id if isinstance(account_id, str) else None

    def invoke(self, prompt: str) -> str:
        """Stream one sentiment-analysis response and return its complete text."""
        if not prompt or not prompt.strip():
            raise ValueError("prompt must not be empty")

        access_token, account_id = self._credentials()
        payload = json.dumps(
            {
                "model": self.model,
                "instructions": DEFAULT_INSTRUCTIONS,
                "input": [
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": prompt}],
                    }
                ],
                "stream": True,
                "store": False,
            }
        ).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "OpenAI-Beta": "responses=experimental",
            "originator": "orbit",
        }
        if account_id:
            headers["ChatGPT-Account-Id"] = account_id

        request = urllib.request.Request(
            self.endpoint, data=payload, headers=headers, method="POST"
        )
        assistant_text: list[str] = []
        completed = False
        try:
            with self._urlopen(request, timeout=self.timeout) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data: "):
                        continue
                    data = line.removeprefix("data: ")
                    if data == "[DONE]":
                        break
                    event = json.loads(data)
                    event_type = event.get("type")
                    if event_type == "response.output_text.delta":
                        delta = event.get("delta", "")
                        if isinstance(delta, str):
                            assistant_text.append(delta)
                    elif event_type == "response.completed":
                        completed = True
                    elif event_type == "error":
                        raise RuntimeError(f"OpenAI streaming error: {event}")
        except urllib.error.HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")
            hint = " Run `codex login` to refresh auth.json." if error.code == 401 else ""
            raise RuntimeError(f"OpenAI HTTP {error.code}: {details}.{hint}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"OpenAI request failed: {error.reason}") from error
        except json.JSONDecodeError as error:
            raise RuntimeError("OpenAI returned an invalid streaming event") from error

        output_text = "".join(assistant_text).strip()
        if not completed:
            raise RuntimeError("OpenAI stream ended before response.completed")
        if not output_text:
            raise RuntimeError("OpenAI returned an empty response")
        logger.info("OpenAI OAuth response generated with %s", self.model)
        return output_text
