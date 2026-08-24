from datetime import date, datetime, timezone
import unittest
from unittest.mock import MagicMock, patch

from orbit.core.testnet_reporter import (
    GitHubProjectClient,
    TestnetDailyReporter as DailyReporter,
    _split_report,
    build_report_body,
    build_weekly_report_body,
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
        self.assertIn("Accepted signals: **1**", body)
        self.assertIn("Orders submitted: **0**", body)
        self.assertIn("Order-stage rejections: **1**", body)
        self.assertIn("No-signal evaluations (counted, not expanded): **1**", body)
        self.assertNotIn("quiet-1", body)

    def test_large_report_is_split_without_losing_evidence(self):
        body = "header\n" + "\n".join(f"decision-{index}" for index in range(100))
        parts = _split_report(body, limit=100)
        self.assertGreater(len(parts), 1)
        self.assertEqual("\n".join(parts), body)

    def test_weekly_report_separates_signals_submissions_and_fills(self):
        decisions = [
            {
                "symbol": "ETHUSDT",
                "strategy": "orbit.strategies.ETHStrategy",
                "outcome": "accepted",
                "execution_events": [
                    {"status": "protective_order_submitted"},
                    {"status": "order_submitted"},
                    {"status": "order_filled"},
                ],
            },
            {
                "symbol": "PAXGUSDT",
                "strategy": "orbit.strategies.PAXGUSDTStrategy",
                "outcome": "accepted",
                "execution_events": [
                    {"status": "order_rejected", "reason": "position_notional_limit"},
                    {"status": "protective_order_failed"},
                ],
            },
            {"symbol": "ETHUSDT", "outcome": "rejected"},
        ]
        income = [
            {
                "time": 1,
                "symbol": "ETHUSDT",
                "incomeType": "REALIZED_PNL",
                "income": "10",
            },
            {
                "time": 2,
                "symbol": "ETHUSDT",
                "incomeType": "COMMISSION",
                "income": "-1",
            },
            {
                "time": 3,
                "symbol": "PAXGUSDT",
                "incomeType": "REALIZED_PNL",
                "income": "-5",
            },
        ]

        body = build_weekly_report_body(date(2026, 8, 17), decisions, income)

        self.assertIn("Orders submitted: **1**", body)
        self.assertIn("Orders filled: **1**", body)
        self.assertIn("Order-stage rejections: **1** (50.00%", body)
        self.assertIn("Realized-PnL events: **2**", body)
        self.assertIn("Realized-PnL profit factor: **2.00**", body)
        self.assertIn("Maximum ledger drawdown: **6.00000000 USDT**", body)
        self.assertIn("Protective-order failures: **1**", body)
        self.assertIn("| ETHUSDT | 10.00000000 | -1.00000000", body)


class TestDailyReporter(unittest.TestCase):
    @patch("orbit.core.testnet_reporter.time_module.sleep")
    @patch("orbit.core.testnet_reporter.datetime")
    def test_weekly_report_is_published_on_monday_only(self, datetime_mock, sleep_mock):
        datetime_mock.now.return_value = datetime(2026, 8, 24, 12, tzinfo=timezone.utc)
        sleep_mock.side_effect = RuntimeError("stop loop")
        reporter = DailyReporter(MagicMock(), MagicMock())
        reporter.publish_date = MagicMock(return_value="daily")
        reporter.publish_week = MagicMock(return_value="weekly")

        with self.assertRaisesRegex(RuntimeError, "stop loop"):
            reporter.run_forever(interval_seconds=0)

        reporter.publish_week.assert_called_once_with(date(2026, 8, 17))

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
            recvWindow=60000,
            startTime=int(start.timestamp() * 1000),
            endTime=int(end.timestamp() * 1000) - 1,
            limit=1000,
        )
        mongo.store_income_records.assert_called_once_with([], "testnet")
        mongo.get_income_records.assert_called_once_with(
            int(start.timestamp() * 1000), int(end.timestamp() * 1000), "testnet"
        )
        self.assertEqual(
            github.publish.call_args.args[0], "Orbit Testnet daily report: 2026-08-21"
        )

    def test_weekly_report_reads_exact_completed_utc_week(self):
        mongo = MagicMock()
        mongo.get_trade_decisions.return_value = []
        mongo.get_income_records.return_value = []
        github = MagicMock()
        github.publish.return_value = "https://github.test/report/week"
        reporter = DailyReporter(mongo, github)

        url = reporter.publish_week(date(2026, 8, 17))

        self.assertEqual(url, "https://github.test/report/week")
        start, end, mode = mongo.get_trade_decisions.call_args.args
        self.assertEqual(start, datetime(2026, 8, 17, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 8, 24, tzinfo=timezone.utc))
        self.assertEqual(mode, "testnet")
        self.assertEqual(
            github.publish.call_args.args[0], "Orbit Testnet weekly report: 2026-08-17"
        )
        self.assertFalse(github.publish.call_args.kwargs["autonomous"])


class TestGitHubProjectClient(unittest.TestCase):
    def test_non_autonomous_report_does_not_add_agent_label(self):
        client = GitHubProjectClient.__new__(GitHubProjectClient)
        client.repository = "ipankaj18/Orbit"
        client.project_id = "project-1"
        client._ensure_label = MagicMock()
        created = {
            "title": "weekly",
            "number": 8,
            "node_id": "issue-node",
            "html_url": "https://github.test/issues/8",
            "labels": [{"name": "testnet-report"}],
        }
        client._call = MagicMock(side_effect=[[], created, {}, []])

        client.publish("weekly", "body", autonomous=False)

        client._ensure_label.assert_called_once_with(
            "testnet-report", "1d76db", "Automated Orbit Testnet report"
        )
        self.assertFalse(
            any(
                call.kwargs.get("json", {}).get("labels") == ["ai-autonomous"]
                for call in client._call.call_args_list
            )
        )

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
        client._call = MagicMock(side_effect=[[existing], existing, {}, []])

        url = client.publish("daily", "body")

        self.assertEqual(url, existing["html_url"])
        graphql_call = client._call.call_args_list[2]
        self.assertEqual(graphql_call.args[1], "https://api.github.com/graphql")
        self.assertEqual(
            graphql_call.kwargs["json"]["variables"]["content"], "issue-node"
        )

    def test_obsolete_generated_report_comments_are_removed(self):
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
        stale = {
            "body": "<!-- orbit-testnet-report-part:2 -->\nold evidence",
            "url": "https://api.github.test/comments/2",
        }
        client._call = MagicMock(side_effect=[[existing], existing, {}, [stale], {}])

        client.publish("daily", "corrected short body")

        delete_call = client._call.call_args_list[-1]
        self.assertEqual(delete_call.args, ("DELETE", stale["url"]))


if __name__ == "__main__":
    unittest.main()
