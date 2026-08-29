import json
from datetime import datetime, timezone
from types import SimpleNamespace
import urllib.error
import urllib.parse
from unittest.mock import MagicMock

import pytest

from orbit.market_intelligence.llm.llm_endpoint import LLM
from orbit.market_intelligence.llm.antigravity_client import AntigravityClient
from orbit.market_intelligence.llm.openai_client import (
    DEFAULT_INSTRUCTIONS,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_OPENAI_MODEL,
    CodexOAuthResponsesClient,
    OpenAIResponsesClient,
)


def _redis_mock() -> MagicMock:
    redis_client = MagicMock()
    redis_client.exists.return_value = True
    redis_client.ttl.return_value = 100
    redis_client.get.return_value = "10"
    return redis_client


def test_openai_client_uses_responses_api() -> None:
    sdk_client = MagicMock()
    sdk_client.responses.create.return_value = SimpleNamespace(
        status="completed",
        incomplete_details=None,
        output_text='{"sentiment":"NEUTRAL"}',
    )
    client = OpenAIResponsesClient(client=sdk_client)

    result = client.invoke("Classify this market")

    assert result == '{"sentiment":"NEUTRAL"}'
    sdk_client.responses.create.assert_called_once_with(
        model=DEFAULT_OPENAI_MODEL,
        instructions=DEFAULT_INSTRUCTIONS,
        input="Classify this market",
        max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
    )


def test_openai_client_rejects_empty_output() -> None:
    sdk_client = MagicMock()
    sdk_client.responses.create.return_value = SimpleNamespace(
        status="completed", incomplete_details=None, output_text=" "
    )
    client = OpenAIResponsesClient(client=sdk_client)

    with pytest.raises(RuntimeError, match="empty response"):
        client.invoke("Classify this market")


def test_openai_client_rejects_nonempty_incomplete_output() -> None:
    sdk_client = MagicMock()
    sdk_client.responses.create.return_value = SimpleNamespace(
        status="incomplete",
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        output_text='{"sentiment":"BULLISH"',
    )
    client = OpenAIResponsesClient(client=sdk_client)

    with pytest.raises(RuntimeError, match="status 'incomplete'"):
        client.invoke("Classify this market")


def test_openai_client_uses_web_search_tool() -> None:
    sdk_client = MagicMock()
    sdk_client.responses.create.return_value = SimpleNamespace(
        status="completed", incomplete_details=None, output_text="web result"
    )
    client = OpenAIResponsesClient(client=sdk_client)

    assert client.invoke_web_search("Assess markets") == "web result"
    sdk_client.responses.create.assert_called_once_with(
        model=DEFAULT_OPENAI_MODEL,
        instructions=DEFAULT_INSTRUCTIONS,
        input="Assess markets",
        tools=[{"type": "web_search"}],
        max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
    )


def test_codex_oauth_client_streams_responses(tmp_path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        json.dumps({"tokens": {"access_token": "secret", "account_id": "acct"}}),
        encoding="utf-8",
    )

    class StreamingResponse:
        def __enter__(self):
            return iter(
                [
                    b'data: {"type":"response.output_text.delta","delta":"BULL"}\n',
                    b'data: {"type":"response.output_text.delta","delta":"ISH"}\n',
                    b'data: {"type":"response.completed"}\n',
                    b"data: [DONE]\n",
                ]
            )

        def __exit__(self, *_args):
            return False

    requests = []

    def urlopen(request, timeout):
        requests.append((request, timeout))
        return StreamingResponse()

    client = CodexOAuthResponsesClient(auth_file=auth_file, urlopen=urlopen)

    assert client.invoke("Classify this market") == "BULLISH"
    request, timeout = requests[0]
    assert timeout == 60.0
    assert request.get_header("Authorization") == "Bearer secret"
    assert request.get_header("Chatgpt-account-id") == "acct"
    payload = json.loads(request.data)
    assert payload == {
        "model": DEFAULT_OPENAI_MODEL,
        "instructions": DEFAULT_INSTRUCTIONS,
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": "Classify this market"}],
            }
        ],
        "stream": True,
        "store": False,
    }


def test_codex_oauth_client_requires_access_token(tmp_path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    client = CodexOAuthResponsesClient(auth_file=auth_file)

    with pytest.raises(RuntimeError, match="access token"):
        client.invoke("Classify this market")


def test_codex_oauth_client_enables_external_web_search(tmp_path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        json.dumps({"tokens": {"access_token": "secret", "account_id": "acct"}}),
        encoding="utf-8",
    )

    class StreamingResponse:
        def __enter__(self):
            return iter(
                [
                    b'data: {"type":"response.output_text.delta","delta":"result"}\n',
                    b'data: {"type":"response.completed"}\n',
                    b"data: [DONE]\n",
                ]
            )

        def __exit__(self, *_args):
            return False

    requests = []

    def urlopen(request, timeout):
        requests.append((request, timeout))
        return StreamingResponse()

    client = CodexOAuthResponsesClient(auth_file=auth_file, urlopen=urlopen)

    assert client.invoke_web_search("Assess markets") == "result"
    request, timeout = requests[0]
    payload = json.loads(request.data)
    assert timeout == 300.0
    assert payload["tools"] == [{"type": "web_search", "external_web_access": True}]
    assert payload["include"] == [
        "reasoning.encrypted_content",
        "web_search_call.action.sources",
    ]
    assert request.get_header("Session-id")
    assert request.get_header("Thread-id")


def test_codex_oauth_client_retries_stream_without_completed_event(tmp_path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        json.dumps({"tokens": {"access_token": "secret"}}), encoding="utf-8"
    )

    class StreamingResponse:
        def __init__(self, lines):
            self.lines = lines

        def __enter__(self):
            return iter(self.lines)

        def __exit__(self, *_args):
            return False

    responses = iter(
        [
            StreamingResponse(
                [
                    b'data: {"type":"response.output_text.delta","delta":"partial"}\n',
                    b"data: [DONE]\n",
                ]
            ),
            StreamingResponse(
                [
                    b'data: {"type":"response.output_text.delta","delta":"complete"}\n',
                    b'data: {"type":"response.completed"}\n',
                    b"data: [DONE]\n",
                ]
            ),
        ]
    )
    requests = []

    def urlopen(request, timeout):
        requests.append(request)
        return next(responses)

    client = CodexOAuthResponsesClient(auth_file=auth_file, urlopen=urlopen)

    assert client.invoke_web_search("Assess markets") == "complete"
    assert len(requests) == 2
    assert requests[0].get_header("X-client-request-id") != requests[1].get_header(
        "X-client-request-id"
    )


def test_codex_oauth_client_rejects_repeated_premature_streams(tmp_path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        json.dumps({"tokens": {"access_token": "secret"}}), encoding="utf-8"
    )

    class StreamingResponse:
        def __enter__(self):
            return iter([b"data: [DONE]\n"])

        def __exit__(self, *_args):
            return False

    urlopen = MagicMock(side_effect=lambda _request, timeout: StreamingResponse())
    client = CodexOAuthResponsesClient(auth_file=auth_file, urlopen=urlopen)

    with pytest.raises(RuntimeError, match="before response.completed"):
        client.invoke("Classify this market")

    assert urlopen.call_count == 2


def test_antigravity_client_uses_google_search_with_valid_token(tmp_path) -> None:
    token_file = tmp_path / "token.json"
    token_file.write_text(
        json.dumps(
            {
                "token": {
                    "access_token": "access-token",
                    "refresh_token": "refresh-token",
                    "expiry": "2999-01-01T00:00:00+00:00",
                }
            }
        ),
        encoding="utf-8",
    )
    project_file = tmp_path / "project.txt"
    project_file.write_text("orbit-project\n", encoding="utf-8")
    requests = []

    class JsonResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "response": {
                        "candidates": [
                            {
                                "content": {
                                    "parts": [
                                        {"text": "internal", "thought": True},
                                        {"text": '{"sentiment":"NEUTRAL"}'},
                                    ]
                                }
                            }
                        ]
                    }
                }
            ).encode("utf-8")

    def urlopen(request, timeout):
        requests.append((request, timeout))
        return JsonResponse()

    client = AntigravityClient(
        token_file=token_file,
        project_file=project_file,
        urlopen=urlopen,
    )

    assert client.invoke_web_search("Assess markets") == '{"sentiment":"NEUTRAL"}'
    request, timeout = requests[0]
    payload = json.loads(request.data)
    assert timeout == 120.0
    assert request.full_url == (
        "https://daily-cloudcode-pa.googleapis.com/v1internal:generateContent"
    )
    assert request.get_header("Authorization") == "Bearer access-token"
    assert payload["project"] == "orbit-project"
    assert payload["request"]["tools"] == [{"googleSearch": {}}]


def test_antigravity_client_refreshes_expired_token(tmp_path) -> None:
    token_file = tmp_path / "token.json"
    token_file.write_text(
        json.dumps(
            {
                "token": {
                    "access_token": "expired",
                    "refresh_token": "refresh-token",
                    "expiry": "2000-01-01T00:00:00Z",
                }
            }
        ),
        encoding="utf-8",
    )
    responses = iter(
        [
            {"access_token": "fresh", "expires_in": 3600},
            {
                "response": {
                    "candidates": [{"content": {"parts": [{"text": "BULLISH"}]}}]
                }
            },
        ]
    )

    class JsonResponse:
        def __init__(self, data):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.data).encode("utf-8")

    requests = []

    def urlopen(request, timeout):
        requests.append(request)
        return JsonResponse(next(responses))

    client = AntigravityClient(
        token_file=token_file,
        project="orbit-project",
        oauth_client_id="client-id",
        oauth_client_secret="client-secret",
        urlopen=urlopen,
    )

    assert client.invoke("Assess markets") == "BULLISH"
    assert urllib.parse.parse_qs(requests[0].data.decode("utf-8"))["refresh_token"] == [
        "refresh-token"
    ]
    saved_token = json.loads(token_file.read_text(encoding="utf-8"))["token"]
    assert saved_token["access_token"] == "fresh"
    assert requests[1].get_header("Authorization") == "Bearer fresh"


def test_antigravity_client_uses_refreshed_token_when_secret_is_read_only(
    tmp_path,
) -> None:
    token_file = tmp_path / "token.json"
    token_file.write_text(
        json.dumps(
            {
                "token": {
                    "access_token": "expired",
                    "refresh_token": "refresh-token",
                    "expiry": "2000-01-01T00:00:00Z",
                }
            }
        ),
        encoding="utf-8",
    )
    responses = iter(
        [
            {"access_token": "fresh", "expires_in": 3600},
            {
                "response": {
                    "candidates": [{"content": {"parts": [{"text": "NEUTRAL"}]}}]
                }
            },
        ]
    )

    class JsonResponse:
        def __init__(self, data):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.data).encode("utf-8")

    requests = []

    def urlopen(request, timeout):
        requests.append(request)
        return JsonResponse(next(responses))

    client = AntigravityClient(
        token_file=token_file,
        project="orbit-project",
        oauth_client_id="client-id",
        oauth_client_secret="client-secret",
        urlopen=urlopen,
    )
    client._save_credential = MagicMock(side_effect=PermissionError("read-only"))

    assert client.invoke("Assess markets") == "NEUTRAL"
    assert requests[1].get_header("Authorization") == "Bearer fresh"


@pytest.mark.parametrize(
    "expiry",
    [
        "2026-08-28T15:51:25.123456789Z",
        "2026-08-28T15:51:25.123456789+05:30",
    ],
)
def test_antigravity_client_parses_nanoseconds_without_corrupting_offset(
    expiry,
) -> None:
    parsed = AntigravityClient._parse_expiry(expiry)
    expected = datetime.fromisoformat(expiry.replace("Z", "+00:00")).astimezone(
        timezone.utc
    )

    assert parsed == expected
    assert parsed.microsecond == 123456


def test_antigravity_client_does_not_expose_http_error_body(tmp_path) -> None:
    token_file = tmp_path / "token.json"
    token_file.write_text(
        json.dumps(
            {
                "token": {
                    "access_token": "access-token",
                    "expiry": "2999-01-01T00:00:00+00:00",
                }
            }
        ),
        encoding="utf-8",
    )
    error = urllib.error.HTTPError("https://example.invalid", 500, "error", {}, None)
    error.read = MagicMock(return_value=b"sensitive prompt content")
    client = AntigravityClient(
        token_file=token_file,
        project="orbit-project",
        urlopen=MagicMock(side_effect=error),
    )

    with pytest.raises(RuntimeError, match=r"^Antigravity HTTP 500$") as raised:
        client.invoke("sensitive prompt")

    assert "sensitive" not in str(raised.value)
    error.read.assert_not_called()


def test_llm_prefers_openai_without_startup_request() -> None:
    openai_client = MagicMock()
    openai_client.invoke.return_value = "primary"
    fallback = MagicMock()

    llm = LLM(
        openai_client=openai_client,
        openrouter_client=fallback,
        groq_client=fallback,
        redis_client=_redis_mock(),
    )

    openai_client.invoke.assert_not_called()
    assert llm.invoke("market prompt") == "primary"
    fallback.invoke.assert_not_called()


def test_llm_web_search_prefers_openai_provider() -> None:
    openai_client = MagicMock()
    openai_client.invoke_web_search.return_value = "grounded result"
    fallback = MagicMock()
    llm = LLM(
        openai_client=openai_client,
        antigravity_client=fallback,
        openrouter_client=fallback,
        groq_client=fallback,
        redis_client=_redis_mock(),
    )

    assert llm.invoke_web_search("market prompt") == "grounded result"
    openai_client.invoke_web_search.assert_called_once_with("market prompt")
    fallback.invoke.assert_not_called()


def test_llm_web_search_falls_back_to_antigravity() -> None:
    openai_client = MagicMock()
    openai_client.invoke_web_search.side_effect = RuntimeError("Codex unavailable")
    antigravity_client = MagicMock()
    antigravity_client.invoke_web_search.return_value = "grounded backup"
    llm = LLM(
        openai_client=openai_client,
        antigravity_client=antigravity_client,
        redis_client=_redis_mock(),
    )

    assert llm.invoke_web_search("market prompt") == "grounded backup"
    antigravity_client.invoke_web_search.assert_called_once_with("market prompt")


def test_llm_falls_back_when_openai_fails() -> None:
    openai_client = MagicMock()
    openai_client.invoke.side_effect = RuntimeError("OpenAI unavailable")
    openrouter_client = MagicMock()
    openrouter_client.invoke.return_value = "fallback"

    llm = LLM(
        openai_client=openai_client,
        antigravity_client=MagicMock(
            invoke=MagicMock(side_effect=RuntimeError("Antigravity unavailable"))
        ),
        openrouter_client=openrouter_client,
        groq_client=MagicMock(),
        redis_client=_redis_mock(),
    )

    assert llm.invoke("market prompt") == "fallback"
    openrouter_client.invoke.assert_called_once_with("market prompt")


def test_llm_uses_antigravity_before_other_fallbacks() -> None:
    openai_client = MagicMock()
    openai_client.invoke.side_effect = RuntimeError("Codex unavailable")
    antigravity_client = MagicMock()
    antigravity_client.invoke.return_value = "antigravity backup"
    openrouter_client = MagicMock()
    llm = LLM(
        openai_client=openai_client,
        antigravity_client=antigravity_client,
        openrouter_client=openrouter_client,
        redis_client=_redis_mock(),
    )

    assert llm.invoke("market prompt") == "antigravity backup"
    antigravity_client.invoke.assert_called_once_with("market prompt")
    openrouter_client.invoke.assert_not_called()


def test_llm_loads_antigravity_from_standard_cli_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    token_file = tmp_path / ".gemini/antigravity-cli/antigravity-oauth-token"
    token_file.parent.mkdir(parents=True)
    token_file.write_text(
        json.dumps(
            {
                "token": {
                    "access_token": "access-token",
                    "expiry": "2999-01-01T00:00:00+00:00",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "orbit.market_intelligence.llm.llm_endpoint.default_token_file",
        lambda: token_file,
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_AUTH_FILE", str(tmp_path / "missing-auth.json"))
    monkeypatch.delenv("ANTIGRAVITY_TOKEN_FILE", raising=False)
    monkeypatch.setenv("ANTIGRAVITY_PROJECT", "orbit-project")

    llm = LLM(redis_client=_redis_mock())

    assert isinstance(llm.antigravity_llm, AntigravityClient)


def test_llm_rejects_incomplete_antigravity_only_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    token_file = tmp_path / "token.json"
    token_file.write_text(
        json.dumps(
            {
                "token": {
                    "access_token": "access-token",
                    "expiry": "2999-01-01T00:00:00+00:00",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ANTIGRAVITY_TOKEN_FILE", str(token_file))
    monkeypatch.setenv(
        "ANTIGRAVITY_PROJECT_FILE", str(tmp_path / "missing-project.txt")
    )
    monkeypatch.delenv("ANTIGRAVITY_PROJECT", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_AUTH_FILE", str(tmp_path / "missing-auth.json"))

    with pytest.raises(RuntimeError, match="No market-intelligence LLM configured"):
        LLM(redis_client=_redis_mock())


def test_llm_tries_each_configured_groq_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    first = MagicMock()
    first.invoke.side_effect = RuntimeError("model unavailable")
    second = MagicMock()
    second.invoke.return_value = SimpleNamespace(content="second model")
    third = MagicMock()
    candidates = iter((first, second, third))
    monkeypatch.setattr(
        "orbit.market_intelligence.llm.llm_endpoint.ChatGroq",
        lambda **_kwargs: next(candidates),
    )

    openai_client = MagicMock()
    openai_client.invoke.side_effect = RuntimeError("OpenAI unavailable")
    openrouter_client = MagicMock()
    openrouter_client.invoke.side_effect = RuntimeError("OpenRouter unavailable")
    llm = LLM(
        openai_client=openai_client,
        antigravity_client=MagicMock(
            invoke=MagicMock(side_effect=RuntimeError("Antigravity unavailable"))
        ),
        openrouter_client=openrouter_client,
        redis_client=_redis_mock(),
    )

    assert llm.invoke("market prompt") == "second model"
    first.invoke.assert_called_once_with("market prompt")
    second.invoke.assert_called_once_with("market prompt")
    third.invoke.assert_not_called()


def test_llm_requires_at_least_one_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    for variable in (
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "GROQ_API_KEY",
    ):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.delenv("OPENAI_AUTH_FILE", raising=False)
    monkeypatch.setenv("ANTIGRAVITY_TOKEN_FILE", str(tmp_path / "missing-token"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    with pytest.raises(RuntimeError, match="No market-intelligence LLM configured"):
        LLM(redis_client=_redis_mock())
