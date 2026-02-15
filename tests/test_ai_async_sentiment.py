import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime

from orbit.ai.sentimental_workflow import SentimentWorkflow
from orbit.ai.analysis.reddit_sentiment import RedditSentimentEntry


@pytest.mark.asyncio
async def test_run_analysis_success(monkeypatch):

    # ----------------------------
    # Mock LLM
    # ----------------------------
    mock_llm = MagicMock()

    workflow = SentimentWorkflow(llm=mock_llm)

    # ----------------------------
    # Mock reddit client
    # ----------------------------
    mock_reddit_data = {
        "bitcoin": {
            "posts": [
                {"id": "1", "title": "BTC falling"},
                {"id": "2", "title": "Market bearish"},
            ]
        }
    }

    workflow.fetch_reddit = MagicMock(return_value=mock_reddit_data)
    workflow.calculate_weights = MagicMock(return_value={"bitcoin": 0.8})

    # ----------------------------
    # Mock sentiment analyzer
    # ----------------------------
    fake_sentiment = RedditSentimentEntry(
        sentiment="BEARISH",
        confidence=0.7,
        weight=0.8,
        relevance=0.9,
        post_id="1"
    )

    workflow.reddit_analyzer.analyze_post_sentiment = AsyncMock(
        return_value=fake_sentiment
    )

    workflow.reddit_analyzer.aggregate_weighted_sentiment = MagicMock(
        return_value={
            "overall_score": -0.4,
            "sentiment_label": "BEARISH",
            "confidence": 0.75,
            "total_posts_analyzed": 2,
            "category_breakdown": {},
        }
    )

    workflow.reddit_analyzer.get_top_influential_posts = MagicMock(
        return_value=[]
    )

    # ----------------------------
    # Mock news + indicators
    # ----------------------------
    workflow.fetch_news = MagicMock(return_value="Market looks weak")

    mock_indicators = MagicMock()
    mock_indicators.vix = 20
    mock_indicators.fear_greed_index = 30

    workflow.fetch_indicators = MagicMock(return_value=mock_indicators)

    # ----------------------------
    # Mock DB
    # ----------------------------
    workflow.mongodb.save_sentiment = MagicMock(return_value="mock_id")
    workflow.mongodb.calculate_trends = MagicMock(return_value=None)
    workflow.mongodb.get_trading_signals = MagicMock(
        return_value={"signal": "SELL"}
    )

    # ----------------------------
    # Run workflow
    # ----------------------------
    result = await workflow.run_analysis()

    # ----------------------------
    # Assertions
    # ----------------------------
    assert result["success"] is True
    assert result["database_id"] == "mock_id"
    assert "sentiment" in result
    assert result["sentiment"]["label"] in ["BEARISH", "NEUTRAL", "BULLISH"]

    workflow.mongodb.save_sentiment.assert_called_once()
