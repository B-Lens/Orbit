import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from orbit.core.sentimen_cron import Croner


def _configure_sentiment_redis(redis, state):
    """Model the atomic scripts used by the sentiment scheduler."""
    redis.get.side_effect = state.get

    def eval_script(_script, _key_count, *args):
        market, label_key, base_key, count_key = args[:4]
        if len(args) == 8:
            base, label, _ttl, required = args[4:]
            if state.get(market) != base:
                return 0
            count = (
                int(state.get(count_key, 0)) + 1
                if state.get(label_key) == label and state.get(base_key) == base
                else 1
            )
            if count < int(required):
                state.update({label_key: label, base_key: base, count_key: str(count)})
                return count
            state[market] = label
        elif len(args) == 6:
            expected, sentiment = args[4:]
            if (state.get(market) or "") != expected:
                return 0
            state[market] = sentiment
        else:
            state[market] = args[4]
        for key in (label_key, base_key, count_key):
            state.pop(key, None)
        return -count if len(args) == 8 else 1

    redis.eval.side_effect = eval_script


def _workflow(sentiment="NEUTRAL", confidence=0.72, success=True):
    workflow = MagicMock()
    workflow.run_web_search_analysis = AsyncMock(
        return_value={
            "success": success,
            "sentiment": sentiment,
            "confidence": confidence,
            "explanation": "market evidence",
            **({"error": "web search unavailable"} if not success else {}),
        }
    )
    return workflow


class TestSentimentCron(unittest.TestCase):
    def _run(self, workflow, state=None):
        redis = MagicMock()
        if state is not None:
            _configure_sentiment_redis(redis, state)
        result = asyncio.run(
            Croner(sentiment_workflow=workflow, redis_client=redis).run_once()
        )
        return result, redis

    def test_success_updates_sentiment(self):
        result, redis = self._run(_workflow("BULLISH", 0.9))
        self.assertEqual((result["sentiment"], result["confidence"]), ("BULLISH", 0.9))
        redis.eval.assert_called()

    def test_failed_search_preserves_cached_sentiment(self):
        result, redis = self._run(_workflow(success=False))
        self.assertFalse(result["success"])
        redis.set.assert_not_called()

    def test_neutral_requires_consecutive_confirmation(self):
        state = {"market_sentiments": "BEARISH"}
        workflow = _workflow()
        first, _ = self._run(workflow, state)
        second, _ = self._run(workflow, state)
        self.assertEqual(first["signal_action"], "neutral_pending_confirmation")
        self.assertEqual(second["signal_action"], "neutral_confirmed")
        self.assertEqual(state["market_sentiments"], "NEUTRAL")

    def test_expired_neutral_starts_new_confirmation(self):
        state = {"market_sentiments": "BEARISH"}
        workflow = _workflow()
        self._run(workflow, state)
        for key in (
            "sentiment:pending_label",
            "sentiment:pending_base",
            "sentiment:pending_count",
        ):
            state.pop(key)
        result, _ = self._run(workflow, state)
        self.assertEqual(result["confirmation_count"], 1)
        self.assertEqual(state["market_sentiments"], "BEARISH")

    def test_pending_neutral_does_not_cross_regimes(self):
        state = {"market_sentiments": "BULLISH"}
        workflow = _workflow()
        self._run(workflow, state)
        state["market_sentiments"] = "BEARISH"
        result, _ = self._run(workflow, state)
        self.assertEqual(result["confirmation_count"], 1)
        self.assertEqual(state["market_sentiments"], "BEARISH")

    def test_low_confidence_directional_flip_is_rejected(self):
        state = {"market_sentiments": "BULLISH"}
        result, _ = self._run(_workflow("BEARISH", 0.41), state)
        self.assertEqual(result["signal_action"], "directional_rejected_low_confidence")
        self.assertEqual(result["effective_sentiment"], "BULLISH")

    def test_run_slot_claim_is_atomic_and_fails_closed(self):
        redis = MagicMock()
        croner = Croner(sentiment_workflow=_workflow(), redis_client=redis)
        redis.eval.return_value = 1
        self.assertTrue(croner.claim_sentiment_run_slot(20260822190))
        redis.eval.return_value = 0
        self.assertFalse(croner.claim_sentiment_run_slot(20260822190))
        redis.eval.side_effect = RuntimeError("Redis unavailable")
        self.assertFalse(croner.claim_sentiment_run_slot(20260822191))


if __name__ == "__main__":
    unittest.main()
