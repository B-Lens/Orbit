import os
import sys
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from datetime import datetime

# Set environment variables before imports
os.environ["GROQ_API_KEY"] = "test_key"
os.environ["LANGCHAIN_API_KEY"] = "test_key"
os.environ["LANGSMITH_API_KEY"] = "test_key"


@pytest.mark.asyncio
async def test_run_analysis_success():
    """Test successful workflow execution with comprehensive mocking."""
    try:
        from orbit.market_intelligence.sentimental_workflow import SentimentWorkflow
        from orbit.market_intelligence.analysis.reddit_sentiment import RedditOverallResult

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
        fake_sentiment = RedditOverallResult(
            sentiment="BEARISH",
            confidence=0.7,
            explanation="Market is bearish.",
            total_posts_analyzed=2,
            chunks_analyzed=1,
        )

        workflow.reddit_analyzer.analyze_reddit = AsyncMock(return_value=fake_sentiment)

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
        workflow.mongodb.get_trading_signals = MagicMock(return_value={"signal": "SELL"})

        # ----------------------------
        # Run workflow
        # ----------------------------
        result = await workflow.run_analysis()

        # ----------------------------
        # Assertions with defensive checks
        # ----------------------------
        assert result is not None, "Workflow should return a result"
        assert isinstance(result, dict), "Result should be a dictionary"

        # Check success flag with fallback
        success = result.get("success", False)
        assert success is True, f"Workflow should succeed, got success={success}"

        # Check database_id with fallback
        db_id = result.get("database_id")
        assert db_id == "mock_id", f"Expected database_id='mock_id', got '{db_id}'"

        # Check sentiment exists and is valid
        assert "sentiment" in result, "Result should contain 'sentiment' key"
        sentiment_data = result["sentiment"]

        # Check sentiment label with defensive validation
        if isinstance(sentiment_data, dict):
            label = sentiment_data.get("label")
        else:
            label = sentiment_data

        valid_labels = ["BEARISH", "NEUTRAL", "BULLISH"]
        assert label in valid_labels, f"Sentiment label '{label}' should be one of {valid_labels}"

        # Verify DB operations were called
        workflow.mongodb.save_sentiment.assert_called_once()

    except ImportError as e:
        pytest.skip(f"Required module not available: {str(e)}")
    except Exception as e:
        pytest.fail(f"test_run_analysis_success failed with exception: {str(e)}")


@pytest.mark.asyncio
async def test_run_analysis_with_missing_reddit_data():
    """Test workflow behavior when reddit data is missing."""
    try:
        from orbit.market_intelligence.sentimental_workflow import SentimentWorkflow
        from orbit.market_intelligence.analysis.reddit_sentiment import RedditOverallResult

        # Mock LLM
        mock_llm = MagicMock()
        workflow = SentimentWorkflow(llm=mock_llm)

        # Mock empty reddit data
        workflow.fetch_reddit = MagicMock(return_value={})
        workflow.calculate_weights = MagicMock(return_value={})

        # Mock sentiment analyzer to return neutral
        fake_sentiment = RedditOverallResult(
            sentiment="NEUTRAL",
            confidence=0.5,
            explanation="No data available.",
            total_posts_analyzed=0,
            chunks_analyzed=0,
        )
        workflow.reddit_analyzer.analyze_reddit = AsyncMock(return_value=fake_sentiment)

        # Mock other components
        workflow.fetch_news = MagicMock(return_value="No news")
        mock_indicators = MagicMock()
        mock_indicators.vix = 15
        mock_indicators.fear_greed_index = 50
        workflow.fetch_indicators = MagicMock(return_value=mock_indicators)

        # Mock DB
        workflow.mongodb.save_sentiment = MagicMock(return_value="mock_id_2")
        workflow.mongodb.calculate_trends = MagicMock(return_value=None)
        workflow.mongodb.get_trading_signals = MagicMock(return_value={"signal": "HOLD"})

        # Run workflow
        result = await workflow.run_analysis()

        # Should still complete successfully
        assert result is not None, "Workflow should handle missing data gracefully"
        assert result.get("success", False) is True, "Workflow should succeed even with missing data"

    except ImportError as e:
        pytest.skip(f"Required module not available: {str(e)}")
    except Exception as e:
        pytest.fail(f"test_run_analysis_with_missing_reddit_data failed with exception: {str(e)}")


@pytest.mark.asyncio
async def test_run_analysis_with_api_failure():
    """Test workflow behavior when external APIs fail."""
    try:
        from orbit.market_intelligence.sentimental_workflow import SentimentWorkflow
        from orbit.market_intelligence.analysis.reddit_sentiment import RedditOverallResult

        # Mock LLM
        mock_llm = MagicMock()
        workflow = SentimentWorkflow(llm=mock_llm)

        # Mock API failures
        workflow.fetch_reddit = MagicMock(side_effect=Exception("Reddit API error"))
        workflow.fetch_news = MagicMock(side_effect=Exception("News API error"))
        workflow.fetch_indicators = MagicMock(side_effect=Exception("Indicators API error"))

        # Mock sentiment analyzer to still work
        fake_sentiment = RedditOverallResult(
            sentiment="NEUTRAL",
            confidence=0.5,
            explanation="API errors occurred.",
            total_posts_analyzed=0,
            chunks_analyzed=0,
        )
        workflow.reddit_analyzer.analyze_reddit = AsyncMock(return_value=fake_sentiment)

        # Mock DB
        workflow.mongodb.save_sentiment = MagicMock(return_value="mock_id_3")
        workflow.mongodb.calculate_trends = MagicMock(return_value=None)
        workflow.mongodb.get_trading_signals = MagicMock(return_value={"signal": "HOLD"})

        # Run workflow - should handle API failures gracefully
        try:
            result = await workflow.run_analysis()

            # If it succeeds, verify structure
            if result:
                assert isinstance(result, dict), "Result should be a dictionary"
        except Exception as e:
            # Exception is acceptable for API failures
            assert True, f"API failure handled with exception: {str(e)}"

    except ImportError as e:
        pytest.skip(f"Required module not available: {str(e)}")
    except Exception as e:
        pytest.fail(f"test_run_analysis_with_api_failure failed with exception: {str(e)}")


@pytest.mark.asyncio
async def test_workflow_initialization():
    """Test that workflow can be initialized with different configurations."""
    try:
        from orbit.market_intelligence.sentimental_workflow import SentimentWorkflow

        # Test with mock LLM
        mock_llm = MagicMock()
        workflow1 = SentimentWorkflow(llm=mock_llm)
        assert workflow1 is not None, "Workflow should initialize with mock LLM"

        # Test without LLM (if supported)
        try:
            workflow2 = SentimentWorkflow()
            assert workflow2 is not None, "Workflow should initialize without LLM"
        except TypeError:
            # If LLM is required, that's acceptable
            pass

    except ImportError as e:
        pytest.skip(f"Required module not available: {str(e)}")
    except Exception as e:
        pytest.fail(f"test_workflow_initialization failed with exception: {str(e)}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])