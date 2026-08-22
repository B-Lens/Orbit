import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orbit.market_intelligence.llm.llm_endpoint import LLM
from orbit.market_intelligence.llm.antigravity_client import (
    AntigravityClient,
    DEFAULT_ANTIGRAVITY_AGENT,
)
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
                "content": [
                    {"type": "input_text", "text": "Classify this market"}
                ],
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
    assert payload["tools"] == [
        {"type": "web_search", "external_web_access": True}
    ]
    assert payload["include"] == [
        "reasoning.encrypted_content",
        "web_search_call.action.sources",
    ]
    assert request.get_header("Session-id")
    assert request.get_header("Thread-id")


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


def test_llm_web_search_uses_only_openai_provider() -> None:
    openai_client = MagicMock()
    openai_client.invoke_web_search.return_value = "grounded result"
    fallback = MagicMock()
    llm = LLM(
        openai_client=openai_client,
        openrouter_client=fallback,
        groq_client=fallback,
        redis_client=_redis_mock(),
    )

    assert llm.invoke_web_search("market prompt") == "grounded result"
    openai_client.invoke_web_search.assert_called_once_with("market prompt")
    fallback.invoke.assert_not_called()


def test_antigravity_client_uses_search_tools() -> None:
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "status": "completed",
                    "steps": [
                        {
                            "type": "google_search_result",
                            "result": [{"url": "https://example.com/market"}],
                        },
                        {
                            "type": "model_output",
                            "content": [
                                {"type": "text", "text": "global "},
                                {"type": "text", "text": "crypto result"},
                            ],
                        },
                    ],
                }
            ).encode()

    def urlopen(request, timeout):
        requests.append((request, timeout))
        return Response()

    client = AntigravityClient(api_key="secret", urlopen=urlopen)
    assert client.invoke_web_search("Assess crypto") == "global crypto result"
    request, timeout = requests[0]
    assert timeout == 300.0
    assert request.get_header("X-goog-api-key") == "secret"
    payload = json.loads(request.data)
    assert payload["agent"] == DEFAULT_ANTIGRAVITY_AGENT
    assert payload["tools"] == [
        {"type": "google_search"},
        {"type": "url_context"},
    ]


def test_antigravity_client_rejects_ungrounded_output() -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "status": "completed",
                    "steps": [
                        {
                            "type": "model_output",
                            "content": [{"type": "text", "text": "NEUTRAL"}],
                        }
                    ],
                }
            ).encode()

    client = AntigravityClient(
        api_key="secret", urlopen=lambda *_args, **_kwargs: Response()
    )
    with pytest.raises(RuntimeError, match="no successful web-grounding result"):
        client.invoke_web_search("Assess crypto")


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://generativelanguage.googleapis.com/v1beta/interactions",
        "https://attacker.example/v1beta/interactions",
        "https://generativelanguage.googleapis.com.attacker.example/interactions",
    ],
)
def test_antigravity_client_rejects_untrusted_endpoint(endpoint: str) -> None:
    with pytest.raises(ValueError, match="must use HTTPS"):
        AntigravityClient(api_key="secret", endpoint=endpoint)


def test_llm_web_search_falls_back_to_antigravity() -> None:
    openai_client = MagicMock()
    openai_client.invoke_web_search.side_effect = RuntimeError("OpenAI unavailable")
    antigravity_client = MagicMock()
    antigravity_client.invoke_web_search.return_value = "grounded fallback"
    llm = LLM(
        openai_client=openai_client,
        antigravity_client=antigravity_client,
        redis_client=_redis_mock(),
    )

    assert llm.invoke_web_search("market prompt") == "grounded fallback"
    antigravity_client.invoke_web_search.assert_called_once_with("market prompt")


def test_llm_falls_back_when_openai_fails() -> None:
    openai_client = MagicMock()
    openai_client.invoke.side_effect = RuntimeError("OpenAI unavailable")
    openrouter_client = MagicMock()
    openrouter_client.invoke.return_value = "fallback"

    llm = LLM(
        openai_client=openai_client,
        openrouter_client=openrouter_client,
        groq_client=MagicMock(),
        redis_client=_redis_mock(),
    )

    assert llm.invoke("market prompt") == "fallback"
    openrouter_client.invoke.assert_called_once_with("market prompt")


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
        "ANTIGRAVITY_API_KEY",
        "GEMINI_API_KEY",
    ):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.delenv("OPENAI_AUTH_FILE", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    with pytest.raises(RuntimeError, match="No market-intelligence LLM configured"):
        LLM(redis_client=_redis_mock())
