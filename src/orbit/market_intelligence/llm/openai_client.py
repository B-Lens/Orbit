"""OpenAI Responses API clients for Orbit market intelligence."""

import json
import logging
import os
from pathlib import Path
import urllib.error
import urllib.request
import uuid
from typing import Any, Callable, Optional

from openai import OpenAI

logger = logging.getLogger("Orbit")

DEFAULT_OPENAI_MODEL = "gpt-5.6-luna"
DEFAULT_MAX_OUTPUT_TOKENS = 2_000
MAX_PREMATURE_STREAM_RETRIES = 1
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

    def invoke_web_search(self, prompt: str) -> str:
        """Generate a response grounded by OpenAI's web-search tool."""
        if not prompt or not prompt.strip():
            raise ValueError("prompt must not be empty")

        response = self.client.responses.create(
            model=self.model,
            instructions=DEFAULT_INSTRUCTIONS,
            input=prompt,
            tools=[{"type": "web_search"}],
            max_output_tokens=self.max_output_tokens,
        )
        if response.status != "completed":
            details = getattr(response, "incomplete_details", None)
            raise RuntimeError(
                f"OpenAI web-search response ended with status {response.status!r}: "
                f"{details}"
            )
        output_text = response.output_text
        if not output_text or not output_text.strip():
            raise RuntimeError("OpenAI web search returned an empty response")
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
        web_search_timeout: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        endpoint: Optional[str] = None,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self.auth_file = Path(auth_file).expanduser() if auth_file else default_auth_file()
        self.model = model or os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
        self.timeout = timeout
        self.web_search_timeout = web_search_timeout or float(
            os.getenv("OPENAI_WEB_SEARCH_TIMEOUT", "300")
        )
        if self.web_search_timeout <= 0:
            raise ValueError("OPENAI_WEB_SEARCH_TIMEOUT must be positive")
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
        return self._invoke(prompt, web_search=False)

    def invoke_web_search(self, prompt: str) -> str:
        """Stream one response with live external web search enabled."""
        return self._invoke(prompt, web_search=True)

    def _invoke(self, prompt: str, web_search: bool) -> str:
        if not prompt or not prompt.strip():
            raise ValueError("prompt must not be empty")

        for attempt in range(MAX_PREMATURE_STREAM_RETRIES + 1):
            output_text = self._invoke_stream(prompt, web_search)
            if output_text is not None:
                logger.info("OpenAI OAuth response generated with %s", self.model)
                return output_text
            if attempt < MAX_PREMATURE_STREAM_RETRIES:
                logger.warning(
                    "OpenAI stream ended before response.completed; retrying once"
                )

        raise RuntimeError("OpenAI stream ended before response.completed")

    def _invoke_stream(self, prompt: str, web_search: bool) -> Optional[str]:
        """Run one stream, returning ``None`` when it ends prematurely."""
        access_token, account_id = self._credentials()
        request_id = str(uuid.uuid4())
        payload_data: dict[str, Any] = {
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
        if web_search:
            payload_data.update(
                {
                    "tools": [{"type": "web_search", "external_web_access": True}],
                    "tool_choice": "auto",
                    "parallel_tool_calls": False,
                    "reasoning": {"effort": "medium", "summary": "auto"},
                    "include": [
                        "reasoning.encrypted_content",
                        "web_search_call.action.sources",
                    ],
                }
            )
        payload = json.dumps(payload_data).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "OpenAI-Beta": "responses=experimental",
            "originator": "orbit",
            "session-id": request_id,
            "thread-id": request_id,
            "x-client-request-id": request_id,
        }
        if account_id:
            headers["ChatGPT-Account-Id"] = account_id

        request = urllib.request.Request(
            self.endpoint, data=payload, headers=headers, method="POST"
        )
        assistant_text: list[str] = []
        completed = False
        try:
            request_timeout = self.web_search_timeout if web_search else self.timeout
            with self._urlopen(request, timeout=request_timeout) as response:
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
                    elif event_type in {"error", "response.failed", "response.incomplete"}:
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
            return None
        if not output_text:
            raise RuntimeError("OpenAI returned an empty response")
        return output_text
