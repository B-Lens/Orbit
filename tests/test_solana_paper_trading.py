from datetime import datetime, timezone

import pandas as pd
import pytest

from orbit.paper_trading.solana import close_trade_values, evaluate_exit, summarize_trades


def _trade(signal="BUY"):
    return {
        "symbol": "SOLUSDT",
        "status": "OPEN",
        "signal": signal,
        "entry_price": 100.0,
        "stop_loss": 98.5 if signal == "BUY" else 101.5,
        "take_profit": 103.0 if signal == "BUY" else 97.0,
        "quantity": 0.6,
        "margin_usdt": 30.0,
    }


def test_evaluate_exit_prefers_stop_loss_when_same_candle_hits_both():
    outcome, price = evaluate_exit(
        _trade("BUY"),
        pd.Series({"low": 98.0, "high": 104.0, "close": 102.0}),
    )
    assert outcome == "SL"
    assert price == pytest.approx(98.5)


def test_close_trade_values_is_fee_aware():
    result = close_trade_values(
        _trade("BUY"),
        outcome="Target",
        exit_price=103.0,
        closed_at=datetime.now(timezone.utc),
        fee_rate=0.0004,
    )

    assert result["gross_pnl_usdt"] == pytest.approx(1.8)
    assert result["fees_usdt"] == pytest.approx((60.0 + 61.8) * 0.0004)
    assert result["net_pnl_usdt"] < result["gross_pnl_usdt"]
    assert result["return_on_margin_pct"] > 0


def test_summarize_trades_reports_win_rate_profit_factor_and_drawdown():
    rows = [
        {"status": "CLOSED", "outcome": "Target", "net_pnl_usdt": 2.0, "fees_usdt": 0.1, "r_multiple": 1.8},
        {"status": "CLOSED", "outcome": "SL", "net_pnl_usdt": -1.0, "fees_usdt": 0.1, "r_multiple": -1.1},
        {"status": "CLOSED", "outcome": "Target", "net_pnl_usdt": 1.0, "fees_usdt": 0.1, "r_multiple": 0.9},
    ]

    stats = summarize_trades(rows)

    assert stats["closed_trades"] == 3
    assert stats["wins"] == 2
    assert stats["losses"] == 1
    assert stats["win_rate_pct"] == pytest.approx(66.666666, rel=1e-5)
    assert stats["net_pnl_usdt"] == pytest.approx(2.0)
    assert stats["profit_factor"] == pytest.approx(3.0)
    assert stats["max_drawdown_usdt"] == pytest.approx(1.0)
