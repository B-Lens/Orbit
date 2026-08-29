"""Deterministic, advisory-only reviews for completed trades."""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from orbit.utils.utils import extract_json

logger = logging.getLogger("Orbit")


class PostTradeReviewer:
    """Build and persist an immutable explanation of a completed trade.

    LLM analysis is optional and can only add an advisory explanation. It cannot
    create or activate a trading rule.
    """

    def __init__(self, mongo_handler: Any, llm: Optional[Any] = None) -> None:
        self.mongo_handler = mongo_handler
        self.llm = llm

    @staticmethod
    def _pnl(trade: Dict[str, Any], exit_price: float) -> float:
        entry = float(trade.get("price") or trade.get("entry_price") or 0)
        quantity = abs(float(trade.get("quantity") or 0))
        direction = 1.0 if trade.get("positionSide") == "BUY" else -1.0
        return (exit_price - entry) * quantity * direction

    @staticmethod
    def classify(trade: Dict[str, Any], exit_price: float, net_pnl: float) -> str:
        """Return a stable machine-readable outcome classification."""
        explicit_reason = str(trade.get("exit_reason") or "").lower()
        if explicit_reason:
            return explicit_reason
        if net_pnl >= 0:
            return "profitable_exit"

        side = trade.get("positionSide")
        stop = trade.get("stop_loss_price")
        target = trade.get("target")
        if stop is not None:
            stop_value = float(stop)
            if (side == "BUY" and exit_price <= stop_value) or (
                side == "SELL" and exit_price >= stop_value
            ):
                return "stop_loss"
        if target is not None:
            target_value = float(target)
            if (side == "BUY" and exit_price >= target_value) or (
                side == "SELL" and exit_price <= target_value
            ):
                return "take_profit"
        return "unclassified_loss"

    def _llm_analysis(self, review: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if self.llm is None or review["net_pnl"] >= 0:
            return None
        prompt = (
            "Analyze this completed losing trade. Return JSON with keys "
            "explanation, hypothesis, confidence (0 to 1), and suggested_rule. "
            "The suggestion is advisory only and must not weaken risk limits.\n"
            + json.dumps(review, default=str, sort_keys=True)
        )
        try:
            raw = self.llm.invoke(prompt)
            parsed = extract_json(str(raw))
            if not isinstance(parsed, dict):
                return None
            return {
                "explanation": str(parsed.get("explanation", ""))[:2000],
                "hypothesis": str(parsed.get("hypothesis", ""))[:500],
                "confidence": max(0.0, min(1.0, float(parsed.get("confidence", 0)))),
                "suggested_rule": parsed.get("suggested_rule"),
                "status": "observation",
            }
        except Exception as error:
            logger.warning("Post-trade LLM review failed: %s", error)
            return None

    def review(
        self,
        trade_id: str,
        trade: Dict[str, Any],
        exit_price: float,
        *,
        fees: float = 0.0,
        realized_pnl: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Create and persist one idempotent completed-trade review."""
        gross_pnl = (
            float(realized_pnl)
            if realized_pnl is not None
            else self._pnl(trade, exit_price)
        )
        net_pnl = gross_pnl + float(fees)
        review: Dict[str, Any] = {
            "decision_id": trade_id,
            "symbol": trade.get("symbol"),
            "strategy": trade.get("strategy"),
            "strategy_version": trade.get("strategy_version"),
            "side": trade.get("positionSide"),
            "quantity": abs(float(trade.get("quantity") or 0)),
            "entry_price": float(trade.get("price") or trade.get("entry_price") or 0),
            "exit_price": float(exit_price),
            "opened_at": trade.get("opened_at"),
            "closed_at": datetime.now(timezone.utc),
            "exit_reason": self.classify(trade, exit_price, net_pnl),
            "gross_pnl": gross_pnl,
            "fees": float(fees),
            "net_pnl": net_pnl,
            "pnl_source": "exchange" if realized_pnl is not None else "estimated",
            "market_context": {
                "pattern": trade.get("pattern"),
                "sentiment": trade.get("sentiment"),
                "high": trade.get("high"),
                "low": trade.get("low"),
            },
            "status": "observation",
        }
        analysis = self._llm_analysis(review)
        if analysis is not None:
            review["analysis"] = analysis
        self.mongo_handler.store_trade_review(review)
        return review
