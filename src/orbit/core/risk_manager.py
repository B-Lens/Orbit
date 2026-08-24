"""Deterministic pre-trade risk controls.

These checks are intentionally independent from strategy code.  A strategy may
propose a trade; it cannot bypass portfolio safety policy.
"""

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str
    metrics: dict[str, float]


class PreTradeRiskGuard:
    def __init__(self, policy: Mapping[str, Any] | None = None) -> None:
        policy = policy or {}
        self.max_leverage = int(policy.get("max_leverage", 5))
        self.max_position_notional_pct = float(
            policy.get("max_position_notional_pct", 0.25)
        )
        self.max_risk_per_trade_pct = float(policy.get("max_risk_per_trade_pct", 0.01))
        self.min_reward_risk_ratio = float(policy.get("min_reward_risk_ratio", 1.5))
        self.max_daily_loss_pct = float(policy.get("max_daily_loss_pct", 0.02))

    def evaluate(
        self,
        *,
        equity: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float | None,
        quantity: float,
        leverage: int,
        side: str,
        daily_net_pnl: float = 0.0,
        available_margin: float | None = None,
    ) -> RiskDecision:
        if min(equity, entry_price, stop_loss, quantity) <= 0:
            return RiskDecision(False, "non_positive_input", {})
        if leverage < 1 or leverage > self.max_leverage:
            return RiskDecision(False, "leverage_limit", {"leverage": float(leverage)})
        side = side.upper()
        if side not in ("BUY", "SELL"):
            return RiskDecision(False, "invalid_side", {})
        if (side == "BUY" and stop_loss >= entry_price) or (
            side == "SELL" and stop_loss <= entry_price
        ):
            return RiskDecision(False, "stop_on_wrong_side", {})
        if take_profit is not None and (
            (side == "BUY" and take_profit <= entry_price)
            or (side == "SELL" and take_profit >= entry_price)
        ):
            return RiskDecision(False, "target_on_wrong_side", {})
        if daily_net_pnl <= -(equity * self.max_daily_loss_pct):
            return RiskDecision(
                False, "daily_loss_limit", {"daily_net_pnl": daily_net_pnl}
            )

        stop_distance = abs(entry_price - stop_loss)
        risk_usdt = stop_distance * quantity
        risk_pct = risk_usdt / equity
        position_notional = entry_price * quantity
        notional_pct = position_notional / equity
        required_margin = position_notional / leverage
        margin_capacity = equity if available_margin is None else available_margin
        metrics = {
            "risk_usdt": risk_usdt,
            "risk_pct": risk_pct,
            "notional_pct": notional_pct,
            "required_margin": required_margin,
            "available_margin": margin_capacity,
        }
        if risk_pct > self.max_risk_per_trade_pct:
            return RiskDecision(False, "risk_per_trade_limit", metrics)
        if notional_pct > self.max_position_notional_pct:
            return RiskDecision(False, "position_notional_limit", metrics)
        if required_margin > margin_capacity:
            return RiskDecision(False, "insufficient_margin", metrics)

        if take_profit is not None:
            reward_risk = abs(take_profit - entry_price) / stop_distance
            metrics["reward_risk_ratio"] = reward_risk
            if reward_risk < self.min_reward_risk_ratio:
                return RiskDecision(False, "reward_risk_below_minimum", metrics)
        return RiskDecision(True, "allowed", metrics)
