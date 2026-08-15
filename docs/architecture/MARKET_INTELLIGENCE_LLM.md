# Market-intelligence LLM

Orbit uses the OpenAI Responses API as the primary inference path for Reddit,
Twitter, news, and combined sentiment analysis.

## Decision

The runtime uses `gpt-5.6-terra` by default. It is a general-purpose production
model that balances intelligence and cost. A Codex model or the Codex CLI is
not used in the trading process because Codex is optimized for agentic software
engineering rather than recurring market classification. OpenAI also recommends
GPT-5.6 rather than the rolling ChatGPT `chat-latest` alias for production API
usage.

Official references:

- [OpenAI model selection](https://developers.openai.com/api/docs/models)
- [Chat Latest production recommendation](https://developers.openai.com/api/docs/models/chat-latest)
- [Codex model specialization](https://developers.openai.com/api/docs/models/gpt-5.1-codex)

## Provider order

1. OpenAI Responses API (`OPENAI_API_KEY`, or a provisioned Codex `auth.json`)
2. OpenRouter, when `OPENROUTER_API_KEY` is configured
3. Groq, when `GROQ_API_KEY` is configured

Providers are initialized without making a network request. Each analysis call
tries providers in that fixed order and falls back only after an error or empty
response. This prevents startup probes from consuming tokens or blocking the
sentiment worker during application boot.

## Configuration

```dotenv
OPENAI_API_KEY=...
# Alternative to OPENAI_API_KEY:
OPENAI_AUTH_FILE=/run/secrets/codex/auth.json
OPENAI_MODEL=gpt-5.6-terra
OPENAI_MAX_OUTPUT_TOKENS=2000
```

`OPENAI_MODEL` is configurable so a model can be evaluated and promoted without
a code deployment. `OPENAI_MAX_OUTPUT_TOKENS` bounds response cost and must be a
positive integer. When both authentication methods are present, the API key is
preferred. OAuth credentials are re-read before each call so replacing the
provisioned file refreshes the running worker; the credential must never be
committed to the repository. `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, and `GROQ_API_KEY`
are optional resilience settings. Production should alert when a fallback is
used because different providers may produce different sentiment distributions.

## Operational boundary

The model classifies and explains supplied market data; it does not place an
order. Existing strategy, sentiment-filter, and risk controls remain responsible
for trade decisions. Model changes should be evaluated against a fixed set of
representative prompts before promotion, with attention to JSON validity,
sentiment consistency, latency, and token cost.
