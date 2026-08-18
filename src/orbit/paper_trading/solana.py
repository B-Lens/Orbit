"""SOLUSDT forward-test and backtest runner.

This module is intentionally isolated from Orbit's production order path. It
uses the researched SOL strategy and OHLCV pipeline, writes virtual trades to
MongoDB, accounts for fees, and never calls a Binance order endpoint.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from orbit.core.mongo_handler import MongoHandler
from orbit.strategies.sol_strategy import SolanaVolatilityMomentumStrategy

SYMBOL = "SOLUSDT"
STRATEGY_NAME = "SolanaVolatilityMomentumStrategy"
DEFAULT_MARGIN_USDT = 30.0
DEFAULT_LEVERAGE = 2
# The researched SOL candidate used a two-hour cooldown after an exit.
DEFAULT_COOLDOWN_HOURS = 2
DEFAULT_MAX_HOLD_HOURS = 48
DEFAULT_FEE_RATE = 0.0004  # 4 bps per side; configurable for venue/account tier


def _as_utc_datetime(value: Any) -> datetime:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.to_pydatetime()


def _new_strategy(data: pd.DataFrame) -> SolanaVolatilityMomentumStrategy:
    strategy = SolanaVolatilityMomentumStrategy(data)
    strategy.send_parameters = lambda *args, **kwargs: None  # type: ignore[method-assign]
    return strategy


def evaluate_exit(trade: Dict[str, Any], bar: pd.Series) -> Tuple[Optional[str], Optional[float]]:
    """Resolve hard SL/TP for a candle, preferring SL when intrabar order is unknown."""
    side = trade["signal"]
    sl = float(trade["stop_loss"])
    tp = float(trade["take_profit"])
    low = float(bar["low"])
    high = float(bar["high"])

    if side == "BUY":
        if low <= sl:
            return "SL", sl
        if high >= tp:
            return "Target", tp
    else:
        if high >= sl:
            return "SL", sl
        if low <= tp:
            return "Target", tp
    return None, None


def _atr(data: pd.DataFrame, period: int = 14) -> Optional[float]:
    if data.empty or len(data) < period + 1:
        return None
    previous_close = data["close"].shift(1)
    true_range = pd.concat(
        [
            data["high"] - data["low"],
            (data["high"] - previous_close).abs(),
            (data["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    value = true_range.rolling(period).mean().iloc[-1]
    return None if pd.isna(value) else float(value)


def apply_researched_dynamic_exit(
    trade: Dict[str, Any], data: pd.DataFrame
) -> Tuple[Optional[str], Optional[float], Dict[str, Any]]:
    """Apply momentum reversal exit and an ATR trailing-stop update.

    Hard SL/TP should be evaluated first. A trailing stop derived from a closed
    candle becomes effective for the *next* candle, avoiding look-ahead bias
    about the intrabar order of high/low/close.
    """
    if data.empty:
        return None, None, {}

    close = float(data["close"].iloc[-1])
    side = trade["signal"]
    entry = float(trade["entry_price"])
    latest = pd.Timestamp(data.index[-1])
    trend_reversal = False
    if latest.minute == 45 and latest.hour % 6 == 5:
        trend_data = data.resample("6h").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna()
        if len(trend_data) >= 10:
            trend_fast = trend_data["close"].ewm(span=8, adjust=False).mean()
            fast_ema = float(trend_fast.iloc[-1])
            previous_fast_ema = float(trend_fast.iloc[-2])
            trend_reversal = (
                side == "BUY" and close < fast_ema and fast_ema < previous_fast_ema
            ) or (
                side == "SELL" and close > fast_ema and fast_ema > previous_fast_ema
            )
    current_atr = _atr(data)
    updates: Dict[str, Any] = {}
    initial_risk = abs(entry - float(trade.get("initial_stop_loss", trade["stop_loss"])))

    if side == "BUY":
        if trend_reversal:
            return "Momentum-Reversal", close, updates
        if current_atr is not None:
            best_price = max(float(trade.get("best_price", entry)), close)
            new_stop = best_price - max(initial_risk, 2.0 * current_atr)
            updates["best_price"] = best_price
            if new_stop > float(trade["stop_loss"]):
                updates["stop_loss"] = new_stop
                updates["trailing_stop_active"] = True
    else:
        if trend_reversal:
            return "Momentum-Reversal", close, updates
        if current_atr is not None:
            best_price = min(float(trade.get("best_price", entry)), close)
            new_stop = best_price + max(initial_risk, 2.0 * current_atr)
            updates["best_price"] = best_price
            if new_stop < float(trade["stop_loss"]):
                updates["stop_loss"] = new_stop
                updates["trailing_stop_active"] = True

    return None, None, updates


def close_trade_values(
    trade: Dict[str, Any],
    *,
    outcome: str,
    exit_price: float,
    closed_at: datetime,
    fee_rate: float,
) -> Dict[str, Any]:
    entry = float(trade["entry_price"])
    quantity = float(trade["quantity"])
    margin = float(trade["margin_usdt"])
    side = trade["signal"]

    gross_pnl = (exit_price - entry) * quantity
    if side == "SELL":
        gross_pnl *= -1

    fees = (entry * quantity + exit_price * quantity) * fee_rate
    net_pnl = gross_pnl - fees
    return_on_margin_pct = (net_pnl / margin * 100.0) if margin else 0.0
    initial_stop = float(trade.get("initial_stop_loss", trade["stop_loss"]))
    risk_usdt = abs(entry - initial_stop) * quantity
    r_multiple = (net_pnl / risk_usdt) if risk_usdt else 0.0

    return {
        "status": "CLOSED",
        "outcome": outcome,
        "exit_price": float(exit_price),
        "closed_at": closed_at,
        "gross_pnl_usdt": gross_pnl,
        "fees_usdt": fees,
        "net_pnl_usdt": net_pnl,
        "return_on_margin_pct": return_on_margin_pct,
        "r_multiple": r_multiple,
    }


def summarize_trades(trades: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = [row for row in trades if row.get("status") == "CLOSED"]
    pnls = [float(row.get("net_pnl_usdt", 0.0)) for row in rows]
    winners = [pnl for pnl in pnls if pnl > 0]
    losers = [pnl for pnl in pnls if pnl < 0]

    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)

    gross_profit = sum(winners)
    gross_loss = abs(sum(losers))
    profit_factor = gross_profit / gross_loss if gross_loss else (float("inf") if gross_profit else 0.0)

    return {
        "closed_trades": len(rows),
        "wins": len(winners),
        "losses": len(losers),
        "timeouts": sum(1 for row in rows if row.get("outcome") == "Timeout"),
        "momentum_reversal_exits": sum(
            1 for row in rows if row.get("outcome") == "Momentum-Reversal"
        ),
        "win_rate_pct": (len(winners) / len(rows) * 100.0) if rows else 0.0,
        "net_pnl_usdt": sum(pnls),
        "total_fees_usdt": sum(float(row.get("fees_usdt", 0.0)) for row in rows),
        "avg_pnl_usdt": (sum(pnls) / len(rows)) if rows else 0.0,
        "avg_r_multiple": (
            sum(float(row.get("r_multiple", 0.0)) for row in rows) / len(rows)
            if rows else 0.0
        ),
        "profit_factor": profit_factor,
        "max_drawdown_usdt": max_drawdown,
    }


def _paper_trade_from_signal(
    signal: Dict[str, Any], *, margin_usdt: float, leverage: int, opened_at: datetime, mode: str
) -> Dict[str, Any]:
    entry = float(signal["entry_price"])
    stop = float(signal["stop_loss"])
    notional = margin_usdt * leverage
    return {
        "symbol": SYMBOL,
        "mode": mode,
        "status": "OPEN",
        "signal": signal["signal"],
        "entry_price": entry,
        "initial_stop_loss": stop,
        "stop_loss": stop,
        "take_profit": float(signal["take_profit"]),
        "margin_usdt": margin_usdt,
        "leverage": leverage,
        "notional_usdt": notional,
        "quantity": notional / entry,
        "opened_at": opened_at,
        "best_price": entry,
        "trailing_stop_active": False,
        "pattern": signal.get("pattern"),
        "strategy": STRATEGY_NAME,
        "strategy_meta": signal.get("strategy_meta", {}),
    }


def backtest_solana(
    data: pd.DataFrame,
    *,
    margin_usdt: float = DEFAULT_MARGIN_USDT,
    leverage: int = DEFAULT_LEVERAGE,
    cooldown_hours: int = DEFAULT_COOLDOWN_HOURS,
    max_hold_hours: int = DEFAULT_MAX_HOLD_HOURS,
    fee_rate: float = DEFAULT_FEE_RATE,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Replay the SOL research candidate without touching exchange APIs."""
    if data.empty or len(data) < 170:
        return [], summarize_trades([])

    data = data.sort_index().copy()
    trades: List[Dict[str, Any]] = []
    active: Optional[Dict[str, Any]] = None
    cooldown_until: Optional[datetime] = None

    for i in range(168, len(data)):
        now = _as_utc_datetime(data.index[i])
        bar = data.iloc[i]

        if active is not None:
            outcome, exit_price = evaluate_exit(active, bar)
            updates: Dict[str, Any] = {}
            if outcome is None:
                outcome, exit_price, updates = apply_researched_dynamic_exit(active, data.iloc[: i + 1])
            if updates:
                active.update(updates)
            if outcome is None and now - active["opened_at"] >= timedelta(hours=max_hold_hours):
                outcome, exit_price = "Timeout", float(bar["close"])
            if outcome is not None and exit_price is not None:
                active.update(
                    close_trade_values(
                        active,
                        outcome=outcome,
                        exit_price=exit_price,
                        closed_at=now,
                        fee_rate=fee_rate,
                    )
                )
                trades.append(active)
                active = None
                cooldown_until = now + timedelta(hours=cooldown_hours)
            continue

        if cooldown_until is not None and now < cooldown_until:
            continue

        signal = _new_strategy(data.iloc[: i + 1]).generate_signals(symbol=SYMBOL)
        if signal:
            active = _paper_trade_from_signal(
                signal,
                margin_usdt=margin_usdt,
                leverage=leverage,
                opened_at=now,
                mode="paper_backtest",
            )

    if active is not None:
        final_time = _as_utc_datetime(data.index[-1])
        active.update(
            close_trade_values(
                active,
                outcome="Timeout",
                exit_price=float(data.iloc[-1]["close"]),
                closed_at=final_time,
                fee_rate=fee_rate,
            )
        )
        trades.append(active)

    return trades, summarize_trades(trades)


class SolanaPaperTrader:
    """Persistent 15-minute SOLUSDT forward-test runner backed by MongoDB."""

    def __init__(
        self,
        mongo_handler: Optional[MongoHandler] = None,
        *,
        margin_usdt: float = DEFAULT_MARGIN_USDT,
        leverage: int = DEFAULT_LEVERAGE,
        cooldown_hours: int = DEFAULT_COOLDOWN_HOURS,
        max_hold_hours: int = DEFAULT_MAX_HOLD_HOURS,
        fee_rate: float = DEFAULT_FEE_RATE,
    ) -> None:
        self.mongo = mongo_handler or MongoHandler()
        if not hasattr(self.mongo, "db"):
            raise RuntimeError("MongoDB is required for persistent paper trading")
        self.collection = self.mongo.db["paper_trades"]
        self.collection.create_index([("symbol", 1), ("opened_at", 1)])
        self.collection.create_index([("symbol", 1), ("status", 1)])
        self.margin_usdt = margin_usdt
        self.leverage = leverage
        self.cooldown_hours = cooldown_hours
        self.max_hold_hours = max_hold_hours
        self.fee_rate = fee_rate

    def _open_trade(self) -> Optional[Dict[str, Any]]:
        return self.collection.find_one({"symbol": SYMBOL, "status": "OPEN"})

    def _cooldown_active(self, now: datetime) -> bool:
        latest = self.collection.find_one(
            {"symbol": SYMBOL, "status": "CLOSED"}, sort=[("closed_at", -1)]
        )
        if not latest or not latest.get("closed_at"):
            return False
        return now < latest["closed_at"] + timedelta(hours=self.cooldown_hours)

    def run_once(self) -> Dict[str, Any]:
        data = self.mongo.handle_mongo_data(SYMBOL)
        if data.empty:
            return {"status": "no_data", "symbol": SYMBOL}

        now = _as_utc_datetime(data.index[-1])
        bar = data.iloc[-1]
        active = self._open_trade()

        if active:
            outcome, exit_price = evaluate_exit(active, bar)
            dynamic_updates: Dict[str, Any] = {}
            if outcome is None:
                outcome, exit_price, dynamic_updates = apply_researched_dynamic_exit(active, data)
            if outcome is None and now - active["opened_at"] >= timedelta(hours=self.max_hold_hours):
                outcome, exit_price = "Timeout", float(bar["close"])
            if outcome is not None and exit_price is not None:
                values = close_trade_values(
                    {**active, **dynamic_updates},
                    outcome=outcome,
                    exit_price=exit_price,
                    closed_at=now,
                    fee_rate=self.fee_rate,
                )
                self.collection.update_one(
                    {"_id": active["_id"]}, {"$set": {**dynamic_updates, **values}}
                )
                return {"status": "closed", "symbol": SYMBOL, **values}
            if dynamic_updates:
                self.collection.update_one({"_id": active["_id"]}, {"$set": dynamic_updates})
            return {
                "status": "open",
                "symbol": SYMBOL,
                "entry_price": active["entry_price"],
                "stop_loss": dynamic_updates.get("stop_loss", active["stop_loss"]),
            }

        if self._cooldown_active(now):
            return {"status": "cooldown", "symbol": SYMBOL}

        signal = _new_strategy(data).generate_signals(symbol=SYMBOL)
        if not signal:
            return {"status": "no_signal", "symbol": SYMBOL}

        document = _paper_trade_from_signal(
            signal,
            margin_usdt=self.margin_usdt,
            leverage=self.leverage,
            opened_at=now,
            mode="paper_forward",
        )
        document["fee_rate"] = self.fee_rate
        result = self.collection.insert_one(document)
        return {"status": "opened", "paper_trade_id": str(result.inserted_id), **document}

    def stats(self) -> Dict[str, Any]:
        rows = list(
            self.collection.find({"symbol": SYMBOL, "status": "CLOSED"}).sort("closed_at", 1)
        )
        return {"symbol": SYMBOL, "strategy": STRATEGY_NAME, **summarize_trades(rows)}


def _print_json(value: Dict[str, Any]) -> None:
    def default(obj: Any) -> str:
        if isinstance(obj, datetime):
            return obj.isoformat()
        return str(obj)

    # JSON has no representation for Infinity.
    safe = dict(value)
    if safe.get("profit_factor") == float("inf"):
        safe["profit_factor"] = "Infinity"
    print(json.dumps(safe, indent=2, default=default, allow_nan=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Orbit SOLUSDT paper trading")
    parser.add_argument("--once", action="store_true", help="Run one forward-test cycle")
    parser.add_argument("--loop", action="store_true", help="Run continuously every 15 minutes")
    parser.add_argument("--stats", action="store_true", help="Print persisted paper-trade stats")
    parser.add_argument("--backtest-days", type=int, default=0, help="Backtest recent Binance candles")
    args = parser.parse_args()

    fee_rate = float(os.getenv("ORBIT_PAPER_FEE_RATE", str(DEFAULT_FEE_RATE)))
    margin = float(os.getenv("ORBIT_SOL_PAPER_MARGIN_USDT", str(DEFAULT_MARGIN_USDT)))
    leverage = int(os.getenv("ORBIT_SOL_PAPER_LEVERAGE", str(DEFAULT_LEVERAGE)))
    cooldown = int(os.getenv("ORBIT_SOL_PAPER_COOLDOWN_HOURS", str(DEFAULT_COOLDOWN_HOURS)))
    max_hold = int(os.getenv("ORBIT_SOL_PAPER_MAX_HOLD_HOURS", str(DEFAULT_MAX_HOLD_HOURS)))

    mongo = MongoHandler()
    if args.backtest_days:
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - args.backtest_days * 24 * 60 * 60 * 1000
        data = mongo.data_collector(SYMBOL, interval="15m", start_time=start_ms)
        _, stats = backtest_solana(
            data,
            margin_usdt=margin,
            leverage=leverage,
            cooldown_hours=cooldown,
            max_hold_hours=max_hold,
            fee_rate=fee_rate,
        )
        _print_json({"symbol": SYMBOL, "strategy": STRATEGY_NAME, "days": args.backtest_days, **stats})
        return

    trader = SolanaPaperTrader(
        mongo,
        margin_usdt=margin,
        leverage=leverage,
        cooldown_hours=cooldown,
        max_hold_hours=max_hold,
        fee_rate=fee_rate,
    )
    if args.stats:
        _print_json(trader.stats())
        return
    if args.once or not args.loop:
        _print_json(trader.run_once())
        return

    while True:
        _print_json(trader.run_once())
        time.sleep(15 * 60)


if __name__ == "__main__":
    main()
