from datetime import datetime, timezone

import pandas as pd
import pytest

from orbit.paper_trading.solana import (
    apply_researched_dynamic_exit,
    close_trade_values,
    evaluate_exit,
    summarize_trades,
)


def _trade(signal="BUY"):
    stop = 98.5 if signal == "BUY" else 101.5
    return {
        "symbol": "SOLUSDT",
        "status": "OPEN",
        "signal": signal,
        "entry_price": 100.0,
        "initial_stop_loss": stop,
        "stop_loss": stop,
        "take_profit": 103.0 if signal == "BUY" else 97.0,
        "quantity": 0.6,
        "margin_usdt": 30.0,
        "best_price": 100.0,
    }


def test_evaluate_exit_prefers_stop_loss_when_same_candle_hits_both():
    outcome, price = evaluate_exit(
        _trade("BUY"),
        pd.Series({"low": 98.0, "high": 104.0, "close": 102.0}),
    )
    assert outcome == "SL"
    assert price == pytest.approx(98.5)


def test_close_trade_values_is_fee_aware_and_uses_initial_risk():
    trade = _trade("BUY")
    trade["stop_loss"] = 100.5  # trailing stop has already moved above entry
    result = close_trade_values(
        trade,
        outcome="Target",
        exit_price=103.0,
        closed_at=datetime.now(timezone.utc),
        fee_rate=0.0004,
    )

    assert result["gross_pnl_usdt"] == pytest.approx(1.8)
    assert result["fees_usdt"] == pytest.approx((60.0 + 61.8) * 0.0004)
    assert result["net_pnl_usdt"] < result["gross_pnl_usdt"]
    assert result["return_on_margin_pct"] > 0
    expected_initial_risk = (100.0 - 98.5) * 0.6
    assert result["r_multiple"] == pytest.approx(result["net_pnl_usdt"] / expected_initial_risk)


def test_dynamic_exit_takes_profitable_sma_reversion():
    # A profitable long closing >0.5% above its SMA matches the researched
    # mean-reversion exit, without needing to hit the original 2.5R target.
    closes = [100.0] * 24 + [101.0]
    data = pd.DataFrame(
        {
            "open": closes,
            "high": [value + 0.2 for value in closes],
            "low": [value - 0.2 for value in closes],
            "close": closes,
            "volume": [1000.0] * len(closes),
        }
    )
    outcome, price, updates = apply_researched_dynamic_exit(_trade("BUY"), data)
    assert outcome == "SMA-Profit"
    assert price == pytest.approx(101.0)
    assert updates == {}


def test_dynamic_trailing_stop_only_tightens_risk():
    closes = [100.0 + i * 0.01 for i in range(25)]
    data = pd.DataFrame(
        {
            "open": closes,
            "high": [value + 0.3 for value in closes],
            "low": [value - 0.3 for value in closes],
            "close": closes,
            "volume": [1000.0] * len(closes),
        }
    )
    outcome, price, updates = apply_researched_dynamic_exit(_trade("BUY"), data)
    assert outcome is None
    assert price is None
    assert updates["best_price"] > 100.0
    assert updates["stop_loss"] > 98.5
    assert updates["trailing_stop_active"] is True


def test_summarize_trades_reports_win_rate_profit_factor_and_drawdown():
    rows = [
        {"status": "CLOSED", "outcome": "Target", "net_pnl_usdt": 2.0, "fees_usdt": 0.1, "r_multiple": 1.8},
        {"status": "CLOSED", "outcome": "SL", "net_pnl_usdt": -1.0, "fees_usdt": 0.1, "r_multiple": -1.1},
        {"status": "CLOSED", "outcome": "SMA-Profit", "net_pnl_usdt": 1.0, "fees_usdt": 0.1, "r_multiple": 0.9},
    ]

    stats = summarize_trades(rows)

    assert stats["closed_trades"] == 3
    assert stats["wins"] == 2
    assert stats["losses"] == 1
    assert stats["sma_profit_exits"] == 1
    assert stats["win_rate_pct"] == pytest.approx(66.666666, rel=1e-5)
    assert stats["net_pnl_usdt"] == pytest.approx(2.0)
    assert stats["profit_factor"] == pytest.approx(3.0)
    assert stats["max_drawdown_usdt"] == pytest.approx(1.0)
