import asyncio
import json
import os
from unittest.mock import MagicMock, patch
from orbit.llm.llm_endpoint import WebSearchInvocation

os.environ["GROQ_API_KEY"] = "test_key"
os.environ["LANGCHAIN_API_KEY"] = "test_key"
os.environ["LANGSMITH_API_KEY"] = "test_key"


from orbit.market_intelligence.sentimental_workflow import SentimentWorkflow


def test_web_search_analysis_is_validated_and_persisted():
    mock_llm = MagicMock()
    mock_llm.invoke_web_search_with_provider.return_value = WebSearchInvocation(
        content=json.dumps(
            {
                "sentiment": "BULLISH",
                "confidence": 0.82,
                "explanation": "Rates eased while crypto flows strengthened.",
                "sources": ["https://example.com/market-update"],
            }
        ),
        provider="Codex",
    )
    with patch("orbit.market_intelligence.sentimental_workflow.MongoDBManager"):
        workflow = SentimentWorkflow(llm=mock_llm)
    save_sentiment = MagicMock(return_value="record-id")
    workflow.mongodb.save_sentiment = save_sentiment

    result = asyncio.run(workflow.run_web_search_analysis())

    assert result["success"] is True
    assert result["sentiment"] == "BULLISH"
    assert result["source"] == "live_web_search"
    assert result["provider"] == "Codex"
    record = save_sentiment.call_args.args[0]
    assert record.news_sentiment["source"] == "live_web_search"
    assert record.news_sentiment["provider"] == "Codex"
    assert record.news_sentiment["sources"] == ["https://example.com/market-update"]


def test_web_search_analysis_rejects_missing_sources():
    mock_llm = MagicMock()
    mock_llm.invoke_web_search_with_provider.return_value = WebSearchInvocation(
        content=json.dumps(
            {
                "sentiment": "NEUTRAL",
                "confidence": 0.2,
                "explanation": "No sourced evidence.",
                "sources": [],
            }
        ),
        provider="Antigravity",
    )
    with patch("orbit.market_intelligence.sentimental_workflow.MongoDBManager"):
        workflow = SentimentWorkflow(llm=mock_llm)
    workflow.handle_exception = MagicMock()
    save_sentiment = MagicMock()
    workflow.mongodb.save_sentiment = save_sentiment

    result = asyncio.run(workflow.run_web_search_analysis())

    assert result["success"] is False
    save_sentiment.assert_not_called()
