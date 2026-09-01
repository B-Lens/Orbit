"""Versioned prompt retrieval for market intelligence."""

import logging
import os
from typing import Any, Optional

logger = logging.getLogger("Orbit")

try:
    import langfuse

    _LANGFUSE_AVAILABLE = True
except ImportError:
    _LANGFUSE_AVAILABLE = False
    logger.warning("langfuse package not installed; prompt versioning disabled.")


class PromptManager:
    """Retrieve the production prompt from Langfuse or a local fallback."""

    _LOCAL_TEMPLATES = {
        "global_crypto_web_sentiment_v3": (
            "You are Orbit's institutional market-intelligence analyst. The current "
            "UTC time is {current_time_utc}. Use live web search to assess information "
            "published or materially updated during the last four hours. Build a global, "
            "cross-asset view of cryptocurrency sentiment rather than summarizing headlines.\n\n"
            "Cover Asia-Pacific, Europe, and the Americas and account for which sessions are "
            "open or handing off. Prioritize:\n"
            "- central banks, inflation, rates, bonds, USD, equities, gold, and oil\n"
            "- Bitcoin, Ethereum, major crypto assets, ETFs, regulation, and institutional flows\n"
            "- futures funding, open interest, liquidations, basis, leverage, and exchange incidents\n"
            "- spot/ETF flows, stablecoin liquidity, on-chain stress, options skew/volatility, "
            "and breadth across large-cap altcoins\n"
            "- geopolitical or macro events with immediate risk-on/risk-off impact\n\n"
            "Use credible, recent sources. Treat rumors and unsupported social posts as noise. "
            "Assess crypto direction over the next 4-12 hours. First establish the macro and "
            "cross-asset regime; then test whether crypto-specific flows confirm or diverge from "
            "equities, USD, rates, gold, and volatility. Separate observed facts from inference. "
            "Resolve conflicts explicitly, identify the dominant transmission channel into BTC/ETH "
            "and leveraged crypto markets, and name the strongest invalidating risk. Weight "
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
            '  "explanation": "4-7 concise sentences covering global session context, macro regime, '
            "crypto-specific confirmation/divergence, derivatives positioning, counter-evidence, "
            'dominant 4-12 hour scenario, and its key invalidation",\n'
            '  "sources": ["https://source.example/article"]\n'
            "}}\n"
            "Include 2-8 source URLs actually consulted."
        )
    }

    def __init__(self) -> None:
        self.langfuse_client: Optional[Any] = None
        self._ingested: set[str] = set()
        if _LANGFUSE_AVAILABLE:
            try:
                self.langfuse_client = langfuse.Langfuse(
                    public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
                    secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
                    host=os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com"),
                )
                self.langfuse_client.auth_check()
            except Exception as error:
                logger.warning(
                    "Langfuse initialization failed: %s. Using local prompts.",
                    error,
                )
                self.langfuse_client = None

    def get_prompt(self, name: str, **kwargs: Any) -> str:
        """Return a formatted remote prompt or its local fallback."""
        if self.langfuse_client:
            try:
                prompt = self.langfuse_client.get_prompt(name=name)
                if prompt and prompt.prompt:
                    return str(prompt.prompt).format(**kwargs)
            except Exception as error:
                logger.warning("Failed to fetch Langfuse prompt '%s': %s", name, error)

        template = self._LOCAL_TEMPLATES.get(name)
        if template is None:
            raise ValueError(f"No local template for prompt '{name}'")
        self._ensure_ingested(name, template)
        return template.format(**kwargs)

    def _ensure_ingested(self, name: str, template: str) -> None:
        """Best-effort ingestion of the local production prompt into Langfuse."""
        if name in self._ingested:
            return
        self._ingested.add(name)
        if not self.langfuse_client:
            return
        try:
            self.langfuse_client.create_prompt(
                name=name, prompt=template, type="text", labels=["production"]
            )
        except Exception as error:
            logger.warning("Failed to ingest prompt '%s': %s", name, error)
