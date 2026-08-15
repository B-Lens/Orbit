import asyncio
import json
import os
from unittest.mock import MagicMock, patch
os.environ["GROQ_API_KEY"] = "test_key"
os.environ["LANGCHAIN_API_KEY"] = "test_key"
os.environ["LANGSMITH_API_KEY"] = "test_key"


from orbit.market_intelligence.sentimental_workflow import Sentiment, SentimentWorkflow
from orbit.market_intelligence.analysis.reddit_sentiment import RedditOverallResult
from orbit.market_intelligence.analysis.twitter_sentiment import TwitterSentimentResult

    
def test_run_analysis_success():

    # ----------------------------
    # Mock LLM
    # ----------------------------
    mock_llm = MagicMock()

    with (
        patch("orbit.market_intelligence.sentimental_workflow.RedditClient"),
        patch("orbit.market_intelligence.sentimental_workflow.TwitterClient"),
        patch("orbit.market_intelligence.sentimental_workflow.MongoDBManager"),
    ):
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
    fake_sentiment = RedditOverallResult(
        sentiment="BEARISH",
        confidence=0.7,
        explanation="Market is bearish.",
        total_posts_analyzed=2,
        chunks_analyzed=1,
    )

    workflow.analyze_reddit = MagicMock(return_value=fake_sentiment)
    workflow.fetch_twitter = MagicMock(return_value=[])
    workflow.analyze_twitter = MagicMock(
        return_value=TwitterSentimentResult(
            sentiment="NEUTRAL",
            confidence=0.3,
            overall_score=0.0,
            total_tweets_analyzed=0,
            explanation="No tweets available.",
        )
    )

    # ----------------------------
    # Mock news + indicators
    # ----------------------------
    workflow.fetch_news = MagicMock(return_value="Market looks weak")
    workflow.get_market_sentiments = MagicMock()

    mock_indicators = MagicMock()
    mock_indicators.vix = 20
    mock_indicators.fear_greed_index = 30

    workflow.fetch_indicators = MagicMock(return_value=mock_indicators)

    # ----------------------------
    # Mock DB
    # ----------------------------
    workflow.mongodb.get_recent_sentiments = MagicMock(return_value=[])
    workflow._combine_results = MagicMock(
        return_value=Sentiment(
            sentiment="BEARISH",
            confidence=0.7,
            explanation="Market is bearish.",
        )
    )
    workflow._save_to_database = MagicMock(return_value="mock_id")

    # ----------------------------
    # Run workflow
    # ----------------------------
    result = asyncio.run(workflow.run_analysis())

    # ----------------------------
    # Assertions
    # ----------------------------
    assert result["success"] is True
    assert result["database_id"] == "mock_id"
    assert result["sentiment"] == "BEARISH"

    workflow._save_to_database.assert_called_once()


def test_hourly_web_search_analysis_is_validated_and_persisted():
    mock_llm = MagicMock()
    mock_llm.invoke_web_search.return_value = json.dumps(
        {
            "sentiment": "BULLISH",
            "confidence": 0.82,
            "explanation": "Rates eased while crypto flows strengthened.",
            "sources": ["https://example.com/market-update"],
        }
    )
    with (
        patch("orbit.market_intelligence.sentimental_workflow.RedditClient"),
        patch("orbit.market_intelligence.sentimental_workflow.TwitterClient"),
        patch("orbit.market_intelligence.sentimental_workflow.MongoDBManager"),
    ):
        workflow = SentimentWorkflow(llm=mock_llm)
    save_sentiment = MagicMock(return_value="record-id")
    workflow.mongodb.save_sentiment = save_sentiment

    result = asyncio.run(workflow.run_web_search_analysis())

    assert result["success"] is True
    assert result["sentiment"] == "BULLISH"
    assert result["source"] == "chatgpt_web_search"
    record = save_sentiment.call_args.args[0]
    assert record.news_sentiment["source"] == "chatgpt_web_search"
    assert record.news_sentiment["sources"] == [
        "https://example.com/market-update"
    ]


def test_hourly_web_search_analysis_rejects_missing_sources():
    mock_llm = MagicMock()
    mock_llm.invoke_web_search.return_value = json.dumps(
        {
            "sentiment": "NEUTRAL",
            "confidence": 0.2,
            "explanation": "No sourced evidence.",
            "sources": [],
        }
    )
    with (
        patch("orbit.market_intelligence.sentimental_workflow.RedditClient"),
        patch("orbit.market_intelligence.sentimental_workflow.TwitterClient"),
        patch("orbit.market_intelligence.sentimental_workflow.MongoDBManager"),
    ):
        workflow = SentimentWorkflow(llm=mock_llm)
    workflow.handle_exception = MagicMock()
    save_sentiment = MagicMock()
    workflow.mongodb.save_sentiment = save_sentiment

    result = asyncio.run(workflow.run_web_search_analysis())

    assert result["success"] is False
    save_sentiment.assert_not_called()
