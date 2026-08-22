from datetime import date, datetime, timezone
import unittest
from unittest.mock import MagicMock

from orbit.core.testnet_reporter import (
    GitHubProjectClient,
    TestnetDailyReporter as DailyReporter,
    build_report_body,
)


class TestReportRendering(unittest.TestCase):
    def test_includes_every_trade_attempt_and_exact_rejection(self):
        decisions = [
            {
                "decision_id": "accepted-1",
                "timestamp": datetime(2026, 8, 21, 1, tzinfo=timezone.utc),
                "execution_mode": "testnet",
                "symbol": "BTCUSDT",
                "signal": "BUY",
                "entry_price": 100,
                "stop_loss": 98,
                "take_profit": 104,
                "sentiment": "BULLISH",
                "strategy": "orbit.strategies.TestStrategy",
                "outcome": "accepted",
                "reason": "passed_filters",
                "execution_events": [
                    {"status": "order_rejected", "reason": "minimum_notional"}
                ],
            },
            {
                "decision_id": "blocked-1",
                "timestamp": datetime(2026, 8, 21, 2, tzinfo=timezone.utc),
                "execution_mode": "testnet",
                "symbol": "ETHUSDT",
                "signal": "SELL",
                "outcome": "rejected",
                "reason": "sentiment_conflict",
            },
            {"decision_id": "quiet-1", "outcome": "no_signal"},
        ]

        body = build_report_body(date(2026, 8, 21), decisions, [])

        self.assertIn("accepted-1", body)
        self.assertIn("blocked-1", body)
        self.assertIn("minimum_notional", body)
        self.assertIn("sentiment_conflict", body)
        self.assertIn("No-signal evaluations (counted, not expanded): **1**", body)
        self.assertNotIn("quiet-1", body)


class TestDailyReporter(unittest.TestCase):
    def test_reads_only_testnet_window_and_publishes_idempotent_title(self):
        mongo = MagicMock()
        mongo.get_trade_decisions.return_value = []
        mongo.get_income_records.return_value = []
        github = MagicMock()
        github.publish.return_value = "https://github.test/report/1"
        futures = MagicMock()
        futures.get_income_history.return_value = []
        reporter = DailyReporter(mongo, github, futures)

        url = reporter.publish_date(date(2026, 8, 21))

        self.assertEqual(url, "https://github.test/report/1")
        start, end, mode = mongo.get_trade_decisions.call_args.args
        self.assertEqual(start, datetime(2026, 8, 21, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 8, 22, tzinfo=timezone.utc))
        self.assertEqual(mode, "testnet")
        futures.get_income_history.assert_called_once_with(
            recvWindow=60000, startTime=int(start.timestamp() * 1000)
        )
        mongo.store_income_records.assert_called_once_with([], "testnet")
        mongo.get_income_records.assert_called_once_with(
            int(start.timestamp() * 1000), int(end.timestamp() * 1000), "testnet"
        )
        self.assertEqual(
            github.publish.call_args.args[0], "Orbit Testnet daily report: 2026-08-21"
        )


class TestGitHubProjectClient(unittest.TestCase):
    def test_existing_issue_is_retried_into_project(self):
        client = GitHubProjectClient.__new__(GitHubProjectClient)
        client.repository = "ipankaj18/Orbit"
        client.project_id = "project-1"
        client._ensure_label = MagicMock()
        existing = {
            "title": "daily",
            "number": 7,
            "node_id": "issue-node",
            "html_url": "https://github.test/issues/7",
            "labels": [{"name": "testnet-report"}, {"name": "ai-autonomous"}],
        }
        client._call = MagicMock(side_effect=[[existing], existing, {}])

        url = client.publish("daily", "body")

        self.assertEqual(url, existing["html_url"])
        graphql_call = client._call.call_args_list[2]
        self.assertEqual(graphql_call.args[1], "https://api.github.com/graphql")
        self.assertEqual(
            graphql_call.kwargs["json"]["variables"]["content"], "issue-node"
        )


if __name__ == "__main__":
    unittest.main()
