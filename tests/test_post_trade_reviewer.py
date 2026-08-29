from unittest.mock import MagicMock

from orbit.core.post_trade_reviewer import PostTradeReviewer


def _losing_long():
    return {
        "symbol": "ETHUSDT",
        "positionSide": "BUY",
        "quantity": 0.1,
        "price": 3500,
        "stop_loss_price": 3430,
        "target": 3605,
        "strategy": "orbit.strategies.eth_strategy.EthStrategy",
        "pattern": "momentum",
        "sentiment": "BULLISH",
    }


def test_losing_stop_is_classified_and_stored():
    mongo = MagicMock()
    reviewer = PostTradeReviewer(mongo)
    review = reviewer.review("decision-1", _losing_long(), 3430)
    assert review["exit_reason"] == "stop_loss"
    assert review["gross_pnl"] == -7
    assert review["net_pnl"] == -7
    assert review["pnl_source"] == "estimated"
    mongo.store_trade_review.assert_called_once_with(review)


def test_exchange_pnl_and_fees_override_estimate():
    reviewer = PostTradeReviewer(MagicMock())
    review = reviewer.review(
        "decision-1", _losing_long(), 3430, realized_pnl=-6.5, fees=-0.5
    )
    assert review["gross_pnl"] == -6.5
    assert review["net_pnl"] == -7
    assert review["pnl_source"] == "exchange"


def test_llm_analysis_remains_an_observation():
    llm = MagicMock()
    llm.invoke.return_value = (
        '{"explanation":"Entered against trend","hypothesis":"counter trend",'
        '"confidence":0.8,"suggested_rule":{"type":"block_setup"}}'
    )
    review = PostTradeReviewer(MagicMock(), llm).review(
        "decision-1", _losing_long(), 3430
    )
    assert review["analysis"]["status"] == "observation"
    assert review["analysis"]["suggested_rule"]["type"] == "block_setup"


def test_profitable_trade_does_not_invoke_llm():
    llm = MagicMock()
    review = PostTradeReviewer(MagicMock(), llm).review(
        "decision-2", _losing_long(), 3605
    )
    assert review["exit_reason"] == "profitable_exit"
    assert "analysis" not in review
    llm.invoke.assert_not_called()
