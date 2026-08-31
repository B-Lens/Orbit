"""Core-owned LLM decisions for trade entry and post-exit review."""

from dataclasses import asdict, dataclass
import json
import logging
from typing import Any, Mapping, Protocol

from orbit.utils.utils import extract_json

logger = logging.getLogger("Orbit")


class LLMClient(Protocol):
    """Minimal interface required by the execution core."""

    def invoke(self, prompt: str) -> str:
        """Return an LLM response for ``prompt``."""


@dataclass(frozen=True)
class EntryReasoning:
    take_trade: bool
    reasoning: str
    confidence: float


@dataclass(frozen=True)
class ExitReasoning:
    outcome: str
    reasoning: str
    confidence: float


class TradeReasoner:
    """Ask an LLM to gate candidate entries and explain completed trades."""

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    @staticmethod
    def _json_response(raw: str) -> Mapping[str, Any]:
        parsed = extract_json(raw)
        if not isinstance(parsed, dict) or not parsed:
            raise ValueError("LLM did not return a JSON object")
        return parsed

    def review_entry(self, signal: Mapping[str, Any]) -> EntryReasoning:
        prompt = (
            "You are Orbit's final pre-trade risk reviewer. Decide whether this "
            "candidate trade should be taken using the strategy signal and current "
            "market-intelligence sentiment. Reject contradictions, weak setups, and "
            "unsafe or incomplete inputs. Return JSON only with take_trade (boolean), "
            "reasoning (string), and confidence (0..1). Candidate: "
            + json.dumps(dict(signal), default=str, sort_keys=True)
        )
        data = self._json_response(self.llm.invoke(prompt))
        take_trade = data.get("take_trade")
        if not isinstance(take_trade, bool):
            raise ValueError("LLM entry response omitted boolean take_trade")
        reasoning = str(data.get("reasoning", "")).strip()
        if not reasoning:
            raise ValueError("LLM entry response omitted reasoning")
        return EntryReasoning(
            take_trade=take_trade,
            reasoning=reasoning,
            confidence=max(0.0, min(1.0, float(data.get("confidence", 0.0)))),
        )

    def review_exit(self, trade: Mapping[str, Any]) -> ExitReasoning:
        prompt = (
            "You are Orbit's post-trade reviewer. Explain why this completed trade "
            "won or lost using its entry, exit, duration, PNL, strategy, sentiment, "
            "and execution events. Return JSON only with outcome (winning or losing), "
            "reasoning (string), and confidence (0..1). Trade: "
            + json.dumps(dict(trade), default=str, sort_keys=True)
        )
        data = self._json_response(self.llm.invoke(prompt))
        expected = "winning" if float(trade.get("pnl", 0.0)) >= 0 else "losing"
        outcome = str(data.get("outcome", expected)).lower()
        if outcome != expected:
            logger.warning(
                "LLM exit outcome %s corrected to computed %s", outcome, expected
            )
            outcome = expected
        reasoning = str(data.get("reasoning", "")).strip()
        if not reasoning:
            raise ValueError("LLM exit response omitted reasoning")
        return ExitReasoning(
            outcome=outcome,
            reasoning=reasoning,
            confidence=max(0.0, min(1.0, float(data.get("confidence", 0.0)))),
        )

    @staticmethod
    def serialize(reasoning: EntryReasoning | ExitReasoning) -> dict[str, Any]:
        return asdict(reasoning)
