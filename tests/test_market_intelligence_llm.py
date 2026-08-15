import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orbit.market_intelligence.llm.llm_endpoint import LLM
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
        output_text='{"sentiment":"NEUTRAL"}'
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
    sdk_client.responses.create.return_value = SimpleNamespace(output_text=" ")
    client = OpenAIResponsesClient(client=sdk_client)

    with pytest.raises(RuntimeError, match="empty response"):
        client.invoke("Classify this market")


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
    assert json.loads(request.data)["stream"] is True


def test_codex_oauth_client_requires_access_token(tmp_path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    client = CodexOAuthResponsesClient(auth_file=auth_file)

    with pytest.raises(RuntimeError, match="access token"):
        client.invoke("Classify this market")


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


def test_llm_requires_at_least_one_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    for variable in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.delenv("OPENAI_AUTH_FILE", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    with pytest.raises(RuntimeError, match="No market-intelligence LLM configured"):
        LLM(redis_client=_redis_mock())
