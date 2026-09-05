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
    workflow = SentimentWorkflow(llm=mock_llm)
    save_sentiment = MagicMock(return_value="record-id")
    workflow.mongodb = MagicMock()
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
    workflow = SentimentWorkflow(llm=mock_llm)
    workflow.handle_exception = MagicMock()
    save_sentiment = MagicMock()
    workflow.mongodb = MagicMock()
    workflow.mongodb.save_sentiment = save_sentiment

    result = asyncio.run(workflow.run_web_search_analysis())

    assert result["success"] is False
    save_sentiment.assert_not_called()


def test_mongodb_connection_failure_does_not_escape_analysis_cycle():
    mock_llm = MagicMock()
    mock_llm.invoke_web_search_with_provider.return_value = WebSearchInvocation(
        content=json.dumps(
            {
                "sentiment": "NEUTRAL",
                "confidence": 0.5,
                "explanation": "Markets are mixed.",
                "sources": ["https://example.com/market-update"],
            }
        ),
        provider="Codex",
    )
    workflow = SentimentWorkflow(llm=mock_llm)
    workflow.handle_exception = MagicMock()

    with patch(
        "orbit.market_intelligence.sentimental_workflow.MongoDBManager",
        side_effect=ConnectionError("Connection refused"),
    ):
        result = asyncio.run(workflow.run_web_search_analysis())

    assert result["success"] is False
    assert result["error"] == "Connection refused"
    assert workflow.mongodb is None
    workflow.handle_exception.assert_called_once()
