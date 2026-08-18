# Market-intelligence LLM

Orbit uses one live, web-grounded OpenAI Responses analysis as the primary
hourly market-intelligence path. It covers global finance, cryptocurrency, and
futures positioning without placing orders or bypassing trading controls.

## Decision

The runtime uses `gpt-5.6-luna` by default. It is a lower-cost model that passed
the live, sourced hourly sentiment schema check. A Codex model or the Codex CLI is
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

The hourly web-search call is intentionally OpenAI-only because OpenRouter and
Groq fallbacks cannot guarantee live web grounding. If it fails, the hourly run
fails closed: the existing Redis sentiment is preserved and the scheduler
retries rather than publishing an ungrounded replacement. Provider fallback
continues to apply to legacy non-web calls.

## Configuration

```dotenv
OPENAI_API_KEY=...
# Alternative to OPENAI_API_KEY:
OPENAI_AUTH_FILE=/run/secrets/codex/auth.json
OPENAI_MODEL=gpt-5.6-luna
OPENAI_MAX_OUTPUT_TOKENS=2000
OPENAI_WEB_SEARCH_TIMEOUT=300
OPENAI_STREAM_MAX_RETRIES=2
OPENAI_STREAM_RETRY_DELAY=1
ORBIT_LEGACY_SENTIMENT_UPDATES=false
```

`OPENAI_MODEL` is configurable so a model can be evaluated and promoted without
a code deployment. `OPENAI_MAX_OUTPUT_TOKENS` bounds response cost and must be a
positive integer. When both authentication methods are present, the API key is
preferred. OAuth credentials are re-read before each call so replacing the
provisioned file refreshes the running worker; the credential must never be
committed to the repository. `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, and `GROQ_API_KEY`
are optional resilience settings. Production should alert when a fallback is
used because different providers may produce different sentiment distributions.
OAuth streaming requests retry server-side `server_error` events up to
`OPENAI_STREAM_MAX_RETRIES` times with exponential backoff starting at
`OPENAI_STREAM_RETRY_DELAY` seconds. Client and validation errors are not
retried, and a failed attempt's partial output is discarded.

## Hourly web-search flow

Once per hour, `Croner.run_once()` asks `SentimentWorkflow` for a fresh web
assessment covering the latest four hours of macro, rates, currencies,
equities, commodities, crypto, futures funding, open interest, liquidations,
regulation, exchange incidents, and geopolitical risk. The result must be valid
JSON with a `BULLISH`, `BEARISH`, or `NEUTRAL` label, bounded confidence,
evidence-based explanation, and up to eight consulted source URLs.

Validated results are stored in MongoDB with `chatgpt_web_search` provenance and
cached in Redis for the trading signal filter. The old RSS, Reddit, and Twitter
poller remains available only when `ORBIT_LEGACY_SENTIMENT_UPDATES=true`; it is
disabled by default to avoid duplicate analysis and uncontrolled web-search
fan-out.

## Operational boundary

The model classifies and explains supplied market data; it does not place an
order. Existing strategy, sentiment-filter, and risk controls remain responsible
for trade decisions. Model changes should be evaluated against a fixed set of
representative prompts before promotion, with attention to JSON validity,
sentiment consistency, latency, and token cost.
