import os
import logging

logger = logging.getLogger("Orbit")

try:
    import langfuse
    _LANGFUSE_AVAILABLE = True
except ImportError:
    _LANGFUSE_AVAILABLE = False
    logger.warning("langfuse package not installed; prompt versioning disabled.")


class PromptManager:
    """Centralised prompt retrieval with Langfuse versioning and local fallback.

    On initialisation the manager attempts to connect to Langfuse using the
    environment variables ``LANGFUSE_PUBLIC_KEY``, ``LANGFUSE_SECRET_KEY`` and
    ``LANGFUSE_HOST``.  If the connection fails or the package is not installed,
    all prompts are served from the local templates defined in this class.

    When a prompt is requested and Langfuse is available but the prompt does not
    yet exist, the manager will attempt to **ingest** the local template into
    Langfuse so that future calls can benefit from versioning.
    """

    # ------------------------------------------------------------------
    # Local prompt templates (identical to the current hard‑coded prompts)
    # ------------------------------------------------------------------
    _LOCAL_TEMPLATES = {
        # Version the production prompt name so an older Langfuse prompt cannot
        # silently override these signal-quality rules after deployment.
        "hourly_web_search_sentiment_v2": (
            "You are Orbit's institutional market-intelligence analyst. The current "
            "UTC time is {current_time_utc}. Use live web search to assess information "
            "published or materially updated during the last four hours.\n\n"
            "Cover global financial markets and cryptocurrency futures, prioritizing:\n"
            "- central banks, inflation, rates, bonds, USD, equities, gold, and oil\n"
            "- Bitcoin, Ethereum, major crypto assets, ETFs, regulation, and institutional flows\n"
            "- futures funding, open interest, liquidations, basis, leverage, and exchange incidents\n"
            "- geopolitical or macro events with immediate risk-on/risk-off impact\n\n"
            "Use credible, recent sources. Treat rumors and unsupported social posts as noise. "
            "Assess the expected direction for risk assets over the next 4-12 hours. Weight "
            "market-moving evidence by recency, source quality, breadth across assets, and likely "
            "price impact. Distinguish genuinely balanced evidence from a quiet news window: use "
            "NEUTRAL only when credible bullish and bearish forces are balanced or there is no "
            "tradable directional edge. A modest but coherent net edge should be BULLISH or "
            "BEARISH with appropriately modest confidence. Do not manufacture direction from "
            "stale, duplicated, speculative, or immaterial items. "
            "This is a market sentiment input, not an instruction to place a trade.\n\n"
            "Return ONLY valid JSON with this exact shape and no markdown:\n"
            "{{\n"
            '  "sentiment": "BULLISH" | "BEARISH" | "NEUTRAL",\n'
            '  "confidence": <float from 0.0 to 1.0>,\n'
            '  "explanation": "2-4 sentences naming the dominant catalysts, counter-evidence, '
            'affected assets, and 4-12 hour risk",\n'
            '  "sources": ["https://source.example/article"]\n'
            "}}\n"
            "Include 2-8 source URLs actually consulted."
        ),
        "reddit_chunk_analysis": (
            "You are a financial sentiment analyst.\n\n"
            "Analyse the following Reddit posts (separated by ---) and determine the\n"
            "**overall** market/crypto sentiment expressed across ALL of them.\n\n"
            "Posts:\n"
            "{snippets}\n\n"
            "Rules:\n"
            "- sentiment MUST be exactly one of: \"BULLISH\", \"BEARISH\", \"NEUTRAL\"\n"
            "- confidence: float 0.0-1.0 reflecting how clearly the posts lean one way (set to 0.0 if completely unrelated to finanial markets)\n"
            "- explanation: concise synthesis of the key themes driving the sentiment\n"
            "- Focus on crypto / financial markets sentiment, not individual stocks\n\n"
            "Respond ONLY with valid JSON (no markdown, no extra text):\n"
            "{{\n"
            "\"sentiment\": \"BULLISH\" | \"BEARISH\" | \"NEUTRAL\",\n"
            "\"confidence\": <float 0.0-1.0>,\n"
            "\"explanation\": \"<concise synthesis>\"\n"
            "}}"
        ),
        "reddit_synthesis": (
            "You are a financial sentiment analyst.\n\n"
            "Below are sentiment summaries from {chunk_count} batches of Reddit posts\n"
            "(covering {total_posts} posts in total).\n\n"
            "{summary_text}\n\n"
            "Synthesise these into a single overall Reddit market sentiment.\n\n"
            "Rules:\n"
            "- sentiment MUST be exactly one of: \"BULLISH\", \"BEARISH\", \"NEUTRAL\"\n"
            "- confidence: float 0.0–1.0\n"
            "- explanation: concise synthesis of the dominant themes\n\n"
            "Respond ONLY with valid JSON (no markdown, no extra text):\n"
            "{{\n"
            "\"sentiment\": \"BULLISH\" | \"BEARISH\" | \"NEUTRAL\",\n"
            "\"confidence\": <float 0.0-1.0>,\n"
            "\"explanation\": \"<concise synthesis>\"\n"
            "}}"
        ),
        "twitter_chunk_analysis": (
            "You are an institutional financial sentiment analyst.\n\n"
            "Below is a batch of financial tweets. Read ALL of them and determine the\n"
            "OVERALL market sentiment they collectively express with respect to tradable\n"
            "assets (crypto, stocks, gold, forex, macro, interest rates, risk sentiment).\n\n"
            "Rules:\n"
            "- BULLISH  → net positive for risk assets / prices likely up\n"
            "- BEARISH  → net negative for risk assets / prices likely down\n"
            "- NEUTRAL  → mixed, unclear, or no meaningful market signal\n"
            "- Base your judgment on the WEIGHT OF EVIDENCE across all tweets, not on\n"
            "any single tweet.\n"
            "- Ignore jokes, memes, and off-topic content.\n"
            "- High-conviction directional language raises confidence.\n"
            "- Speculation words (\"maybe\", \"could\") lower confidence.\n\n"
            "Confidence guidelines:\n"
            "0.9-1.0  : strong, consistent directional signal across most tweets\n"
            "0.7-0.89 : clear directional bias in the majority of tweets\n"
            "0.4-0.69 : weak or mixed directional signal\n"
            "0.0-0.39 : mostly noise / irrelevant\n\n"
            "Tweets:\n"
            "{tweets_block}\n\n"
            "Respond ONLY in valid JSON.\n\n"
            "Rules:\n"
            "- sentiment MUST be exactly one of: \"BULLISH\", \"BEARISH\", \"NEUTRAL\"\n"
            "- Give the confidence about the sentiment <0.0 - 1.0> (0.0 if completely unrelated to financial markets)\n"
            "- Provide the explanation in a field called \"explanation\" (concise synthesis of key themes).\n\n"
            "Respond in Json Format:\n"
            "{{\n"
            "    \"sentiment\": \"BULLISH\",\n"
            "    \"confidence\": 0.0,\n"
            "    \"explanation\": \"brief explanation\"\n"
            "}}"
        ),
        "twitter_synthesis": (
            "You are an institutional financial sentiment analyst.\n\n"
            "Below are sentiment summaries produced from separate batches of financial\n"
            "tweets. Each summary represents the collective sentiment of one batch.\n\n"
            "Your task: synthesise ALL summaries into a single overall market sentiment.\n\n"
            "Rules:\n"
            "- focus on the strongest signals or valid analysis of the chunk.\n"
            "- Weigh each chunk by its confidence score.\n"
            "- BULLISH  → net positive for risk assets\n"
            "- BEARISH  → net negative for risk assets\n"
            "- NEUTRAL  → mixed or no clear signal\n"
            "- Provide a concise explanation that references the key themes.\n"
            "- Ignore failed analysis or No analysis of the chunk\n"
            "- Ignore irrelevant content in the chunk summaries.\n\n"
            "Respond ONLY with valid JSON (no markdown, no extra text):\n"
            "{{\n"
            "\"sentiment\": \"BULLISH\" | \"BEARISH\" | \"NEUTRAL\",\n"
            "\"confidence\": <float 0.0-1.0>,\n"
            "\"reasoning\": \"<concise synthesis explanation>\"\n"
            "}}\n\n"
            "Chunk summaries:\n"
            "{summaries_block}"
        ),
        "news_sentiment": (
            "Analyze overall market sentiment from the following news articles.\n\n"
            "Focus ONLY on MAJOR RECENT EVENTS that can move:\n"
            "- Financial markets\n"
            "- Gold (XAUUSD)\n"
            "- Crypto markets\n\n"
            "Prioritize:\n"
            "- Central bank decisions (Fed, ECB, BOJ, RBI)\n"
            "- Inflation data (CPI, PPI)\n"
            "- Interest rate changes\n"
            "- Geopolitical conflicts / wars\n"
            "- ETF approvals / regulations\n"
            "- Large institutional flows\n"
            "- USD strength / weakness\n"
            "- Recession signals\n"
            "- Liquidity changes\n\n"
            "Decision logic:\n"
            "- No major event → NEUTRAL\n"
            "- One strong major event → BULLISH or BEARISH\n"
            "- Multiple major events same direction → high confidence\n"
            "- Mixed major events → NEUTRAL\n\n"
            "Ignore:\n"
            "- opinions\n"
            "- technical analysis\n"
            "- minor commentary\n"
            "- speculation\n"
            "- duplicate news\n\n"
            "News articles:\n"
            "{news_text}\n\n"
            "Respond ONLY in valid JSON.\n\n"
            "Rules:\n"
            "- sentiment MUST be exactly one of: \"BULLISH\", \"BEARISH\", \"NEUTRAL\"\n"
            "- Give the confidence about the sentiment <0.0 - 1.0> (set to 0.0 if the news is completely unrelated to financial markets)\n"
            "- Provide the explanation in a concise manner, focusing on the key themes and events that led to the sentiment classification.  Avoid generic statements and aim for specific insights derived from the news articles.\n\n"
            "Respond in Json Format:\n"
            "{{\n"
            "    \"sentiment\": \"BULLISH\",\n"
            "    \"confidence\": 0.0,\n"
            "    \"explanation\": \"brief explanation\"\n"
            "}}"
        ),
        "final_sentiment": (
            "You are a senior financial market analyst specializing in sentiment aggregation.\n\n"
            "## TASK\n"
            "Provide a reasoned overall market/crypto sentiment assessment by blending all available signals according to the specified weights.\n\n"
            "## RECENT SENTIMENT HISTORY (for continuity)\n"
            "{memory_section}\n\n"
            "## BLENDING WEIGHTS\n"
            "{weight_dict}\n\n"
            "## SIGNAL SOURCES\n"
            "\n\n"
            "{twitter_section}\n"
            "\n"
            "{reddit_section}\n"
            "\n"
            "{news_section}\n"
            "\n"
            "Indicators: {indicators}\n"
            "\n"
            "Rules:\n"
            "- Blend them using the provided weight dictionary.\n"
            "- sentiment MUST be exactly one of: \"BULLISH\", \"BEARISH\", \"NEUTRAL\"\n"
            "- confidence: float 0.0-1.0 reflecting how clearly the posts lean one way (set to 0.0 if completely unrelated to finanial markets)\n"
            "- explanation: concise synthesis of the key themes driving the sentiment\n"
            "- Focus on crypto / financial markets sentiment, not individual stocks\n\n"
            "Respond ONLY with valid JSON (no markdown, no extra text):\n"
            "{{\n"
            "\"sentiment\": \"BULLISH\" | \"BEARISH\" | \"NEUTRAL\",\n"
            "\"confidence\": <float 0.0-1.0>,\n"
            "\"explanation\": \"<concise synthesis>\"\n"
            "}}"
        ),
        "blend_incremental": (
            "You are a senior financial market analyst specializing in sentiment aggregation.\n\n"
            "## TASK\n"
            "Provide a reasoned overall market/crypto sentiment assessment by blending all available signals according to the specified weights.\n\n"
            "## BLENDING WEIGHTS\n"
            "{weight_dict}\n\n"
            "## SIGNAL SOURCES\n"
            "\n\n"
            "{twitter_section}\n"
            "\n"
            "{reddit_section}\n"
            "\n"
            "{news_section}\n"
            "\n"
            "Rules:\n"
            "- Blend them using the provided weight dictionary.\n"
            "- sentiment MUST be exactly one of: \"BULLISH\", \"BEARISH\", \"NEUTRAL\"\n"
            "- confidence: float 0.0-1.0 reflecting how clearly the posts lean one way (set to 0.0 if completely unrelated to finanial markets)\n"
            "- explanation: concise synthesis of the key themes driving the sentiment\n"
            "- Focus on crypto / financial markets sentiment, not individual stocks\n\n"
            "Respond ONLY with valid JSON (no markdown, no extra text):\n"
            "{{\n"
            "\"sentiment\": \"BULLISH\" | \"BEARISH\" | \"NEUTRAL\",\n"
            "\"confidence\": <float 0.0-1.0>,\n"
            "\"explanation\": \"<concise synthesis>\"\n"
            "}}"
        ),
    }

    def __init__(self) -> None:
        self.langfuse_client = None
        self._ingested: set = set()

        if _LANGFUSE_AVAILABLE:
            try:
                self.langfuse_client = langfuse.Langfuse(
                    public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
                    secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
                    host=os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com"),
                )
                # Quick connectivity check
                self.langfuse_client.auth_check()
                logger.info("Langfuse client initialized successfully.")
            except Exception as exc:
                logger.warning(
                    "Langfuse initialization failed: %s. Falling back to local prompts.",
                    exc,
                )
                self.langfuse_client = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_prompt(self, name: str, **kwargs) -> str:
        """Return a formatted prompt string.

        Tries to fetch the prompt from Langfuse first.  If that fails (or
        Langfuse is unavailable) the local template is used.  When the local
        template is used and Langfuse is available, the manager will attempt
        to **ingest** the template so that future calls can use the versioned
        prompt.

        Args:
            name: Prompt identifier (e.g. ``"reddit_chunk_analysis"``).
            **kwargs: Values to substitute into the template placeholders.

        Returns:
            Formatted prompt string.
        """
        # 1. Try Langfuse
        if self.langfuse_client:
            try:
                prompt_obj = self.langfuse_client.get_prompt(name=name)
                if prompt_obj and prompt_obj.prompt:
                    logger.info("Using Langfuse prompt '%s'", name)
                    return prompt_obj.prompt.format(**kwargs)
            except Exception as exc:
                logger.warning("Failed to fetch Langfuse prompt '%s': %s", name, exc)

        # 2. Fallback to local template
        local_template = self._LOCAL_TEMPLATES.get(name)
        if local_template is None:
            raise ValueError(f"No local template for prompt '{name}'")

        logger.info("Using local fallback prompt '%s'", name)

        # Attempt to ingest the local template if not already done
        self._ensure_ingested(name, local_template)

        return local_template.format(**kwargs)

    # ------------------------------------------------------------------
    # Ingestion helper
    # ------------------------------------------------------------------

    def _ensure_ingested(self, name: str, template: str) -> None:
        """Try to create the prompt in Langfuse if it doesn't already exist.

        This is a best‑effort operation; failures are logged but never raised.
        """
        if name in self._ingested:
            return
        self._ingested.add(name)

        if not self.langfuse_client:
            return

        # Check whether the prompt already exists to avoid duplicates
        try:
            existing = self.langfuse_client.get_prompt(name=name)
            if existing is not None:
                logger.info("Prompt '%s' already exists in Langfuse.", name)
                return
        except Exception:
            pass

        try:
            self.langfuse_client.create_prompt(
                name=name,
                prompt=template,
                type="text",
                labels=["production"]
            )
            logger.info("Ingested prompt '%s' into Langfuse.", name)
        except Exception as exc:
            logger.warning("Failed to ingest prompt '%s': %s", name, exc)
