"""Publish auditable daily Testnet decision reports to a GitHub Project."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
import json
import logging
import os
import time as time_module
from typing import Any, Callable, Iterable, Mapping, Optional

import requests

from orbit.core.performance import PerformanceTracker

logger = logging.getLogger("Orbit")

REPORT_LABEL = "testnet-report"
AGENT_LABEL = "ai-autonomous"
REPORTABLE_OUTCOMES = {"accepted", "rejected", "error"}


def _event_counts(decisions: Iterable[Mapping[str, Any]]) -> Counter[str]:
    return Counter(
        str(event.get("status", "unknown"))
        for row in decisions
        for event in row.get("execution_events", [])
    )


def _income_risk_metrics(
    income_records: Iterable[Mapping[str, Any]],
) -> tuple[Optional[float], float, int]:
    """Return realized-PnL profit factor, max net-income drawdown, and exit events."""
    records = sorted(income_records, key=lambda row: int(row.get("time", 0) or 0))
    realized = [
        float(row.get("income", 0) or 0)
        for row in records
        if str(row.get("incomeType", "")).upper() == "REALIZED_PNL"
        and float(row.get("income", 0) or 0) != 0
    ]
    gross_profit = sum(value for value in realized if value > 0)
    gross_loss = abs(sum(value for value in realized if value < 0))
    profit_factor = gross_profit / gross_loss if gross_loss else None

    equity = peak = max_drawdown = 0.0
    for row in records:
        equity += float(row.get("income", 0) or 0)
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return profit_factor, max_drawdown, len(realized)


def _format_metric(value: Optional[float]) -> str:
    return "N/A" if value is None else f"{value:.2f}"


def _latest_completed_week(today: date) -> date:
    """Return the Monday starting the latest fully completed UTC week."""
    current_week_start = today - timedelta(days=today.weekday())
    return current_week_start - timedelta(days=7)


def _format_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="seconds")
    return str(value).replace("|", "\\|").replace("\n", " ")


def build_report_body(
    report_date: date,
    decisions: Iterable[Mapping[str, Any]],
    income_records: Iterable[Mapping[str, Any]],
) -> str:
    """Render every testnet trade attempt and execution transition as Markdown."""
    all_decisions = list(decisions)
    trade_attempts = [
        row for row in all_decisions if str(row.get("outcome")) in REPORTABLE_OUTCOMES
    ]
    counts = Counter(str(row.get("outcome", "unknown")) for row in all_decisions)
    reasons = Counter(
        str(row.get("reason", "unknown"))
        for row in trade_attempts
        if row.get("outcome") != "accepted"
    )
    for row in trade_attempts:
        for event in row.get("execution_events", []):
            if event.get("status") == "order_rejected":
                reasons[str(event.get("reason", "unknown"))] += 1

    events = _event_counts(trade_attempts)
    performance = PerformanceTracker.summarize(dict(row) for row in income_records)
    lines = [
        f"# Orbit Testnet daily report — {report_date.isoformat()}",
        "",
        "> Generated from MongoDB's immutable decision and income ledgers. "
        "Policy rejections are evidence, not permission to weaken safety limits.",
        "",
        "## Summary",
        "",
        f"- Trade attempts: **{len(trade_attempts)}**",
        f"- Accepted signals: **{counts['accepted']}**",
        f"- Orders submitted: **{events['order_submitted']}**",
        f"- Orders filled: **{events['order_filled']}**",
        f"- Order-stage rejections: **{events['order_rejected']}**",
        f"- Strategy/risk rejections: **{counts['rejected']}**",
        f"- Errors: **{counts['error']}**",
        f"- No-signal evaluations (counted, not expanded): **{counts['no_signal']}**",
        f"- Net P&L after fees/funding: **{performance.net_pnl:.8f} USDT**",
        "",
        "## Rejection and error reasons",
        "",
    ]
    lines.extend(
        [f"- `{reason}`: {count}" for reason, count in sorted(reasons.items())]
        or ["- None"]
    )
    lines.extend(
        [
            "",
            "## Every testnet trade attempt",
            "",
            "| Time (UTC) | Decision | Symbol | Side | Entry | Stop | Target | Sentiment | Strategy | Outcome | Initial reason | Execution events |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in trade_attempts:
        events = (
            "; ".join(
                json.dumps(event, default=str, sort_keys=True)
                for event in row.get("execution_events", [])
            )
            or "—"
        )
        lines.append(
            "| "
            + " | ".join(
                _format_value(value)
                for value in (
                    row.get("timestamp"),
                    row.get("decision_id"),
                    row.get("symbol"),
                    row.get("signal"),
                    row.get("entry_price"),
                    row.get("stop_loss"),
                    row.get("take_profit"),
                    row.get("sentiment"),
                    row.get("strategy"),
                    row.get("outcome"),
                    row.get("reason"),
                    events,
                )
            )
            + " |"
        )
    if not trade_attempts:
        lines.append(
            "| — | — | — | — | — | — | — | — | — | — | — | No trade attempts recorded |"
        )
    lines.extend(
        [
            "",
            "## Codex task",
            "",
            "Analyze repeated rejections and errors against the code and tests. Fix only "
            "a demonstrated software defect. Do not relax risk limits, bypass sentiment, "
            "change an asset to live mode, or expose credentials. If behavior is intentional, "
            "make no code change and explain that conclusion in the workflow artifact.",
        ]
    )
    return "\n".join(lines)


def build_weekly_report_body(
    week_start: date,
    decisions: Iterable[Mapping[str, Any]],
    income_records: Iterable[Mapping[str, Any]],
) -> str:
    """Render one completed UTC week's operational and performance evidence."""
    all_decisions = list(decisions)
    income = list(income_records)
    attempts = [
        row for row in all_decisions if str(row.get("outcome")) in REPORTABLE_OUTCOMES
    ]
    outcomes = Counter(str(row.get("outcome", "unknown")) for row in all_decisions)
    events = _event_counts(attempts)
    performance = PerformanceTracker.summarize(dict(row) for row in income)
    profit_factor, max_drawdown, realized_events = _income_risk_metrics(income)
    accepted = outcomes["accepted"]
    order_rejections = events["order_rejected"]
    rejection_rate = (order_rejections / accepted * 100) if accepted else 0.0
    week_end = week_start + timedelta(days=6)

    symbol_income: dict[str, list[dict[str, Any]]] = {}
    for row in income:
        symbol_income.setdefault(str(row.get("symbol") or "ACCOUNT"), []).append(
            dict(row)
        )
    strategy_rows = Counter(
        (str(row.get("symbol") or "—"), str(row.get("strategy") or "—"))
        for row in attempts
    )

    lines = [
        f"# Orbit Testnet weekly report — {week_start.isoformat()} to {week_end.isoformat()}",
        "",
        "> Completed UTC week. Submitted, filled, and realized-PnL events are reported "
        "separately; none is inferred to mean another.",
        "",
        "## Weekly scorecard",
        "",
        f"- Trade attempts: **{len(attempts)}**",
        f"- Accepted signals: **{accepted}**",
        f"- Orders submitted: **{events['order_submitted']}**",
        f"- Orders filled: **{events['order_filled']}**",
        f"- Order-stage rejections: **{order_rejections}** ({rejection_rate:.2f}% of accepted signals)",
        f"- Strategy/risk rejections: **{outcomes['rejected']}**",
        f"- Errors: **{outcomes['error']}**",
        f"- Realized-PnL events: **{realized_events}**",
        f"- Net P&L after fees/funding: **{performance.net_pnl:.8f} USDT**",
        f"- Realized-PnL profit factor: **{_format_metric(profit_factor)}**",
        f"- Maximum ledger drawdown: **{max_drawdown:.8f} USDT**",
        f"- Protective orders submitted: **{events['protective_order_submitted']}**",
        f"- Protective-order failures: **{events['protective_order_failed']}**",
        "",
        "## Performance by symbol",
        "",
        "| Symbol | Realized P&L | Commission | Funding | Net P&L |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for symbol, records in sorted(symbol_income.items()):
        summary = PerformanceTracker.summarize(records)
        lines.append(
            f"| {_format_value(symbol)} | {summary.realized_pnl:.8f} | "
            f"{summary.commission:.8f} | {summary.funding:.8f} | {summary.net_pnl:.8f} |"
        )
    if not symbol_income:
        lines.append("| — | 0.00000000 | 0.00000000 | 0.00000000 | 0.00000000 |")

    lines.extend(
        [
            "",
            "## Attempts by strategy",
            "",
            "| Symbol | Strategy | Attempts |",
            "| --- | --- | ---: |",
        ]
    )
    for (symbol, strategy), count in sorted(strategy_rows.items()):
        lines.append(
            f"| {_format_value(symbol)} | {_format_value(strategy)} | {count} |"
        )
    if not strategy_rows:
        lines.append("| — | — | 0 |")
    lines.extend(
        [
            "",
            "## Scope notes",
            "",
            "- Slippage is not reported until requested and filled prices are both persisted.",
            "- Realized-PnL rows may represent partial exits, so they are not labelled closed trades.",
            "- Uptime is not inferred from decision frequency; use service telemetry for availability.",
        ]
    )
    return "\n".join(lines)


def _split_report(body: str, limit: int = 60_000) -> list[str]:
    """Split Markdown on line boundaries without dropping report evidence."""
    parts: list[str] = []
    current: list[str] = []
    current_size = 0
    for line in body.splitlines():
        addition = len(line) + 1
        if current and current_size + addition > limit:
            parts.append("\n".join(current))
            current = []
            current_size = 0
        if addition > limit:
            raise ValueError("A single report row exceeds the GitHub body limit")
        current.append(line)
        current_size += addition
    if current:
        parts.append("\n".join(current))
    return parts or [""]


class GitHubProjectClient:
    """Small GitHub REST/GraphQL client with no token exposure in logs."""

    def __init__(
        self,
        token: str,
        repository: str,
        project_id: str,
        request: Callable[..., Any] = requests.request,
    ) -> None:
        self.repository = repository
        self.project_id = project_id
        self._request = request
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _call(self, method: str, url: str, **kwargs: Any) -> Any:
        response = self._request(
            method, url, headers=self._headers, timeout=30, **kwargs
        )
        response.raise_for_status()
        return response.json() if response.content else {}

    def _ensure_label(self, name: str, color: str, description: str) -> None:
        url = f"https://api.github.com/repos/{self.repository}/labels/{name}"
        response = self._request("GET", url, headers=self._headers, timeout=30)
        if response.status_code == 404:
            self._call(
                "POST",
                f"https://api.github.com/repos/{self.repository}/labels",
                json={"name": name, "color": color, "description": description},
            )
            return
        response.raise_for_status()

    def publish(self, title: str, body: str) -> str:
        """Create the daily issue, label it for Codex, and add it to the Project."""
        parts = _split_report(body)
        issue_body = parts[0]
        self._ensure_label(REPORT_LABEL, "1d76db", "Automated Orbit Testnet report")
        self._ensure_label(AGENT_LABEL, "5319e7", "Approved Codex implementation task")
        issues = self._call(
            "GET",
            f"https://api.github.com/repos/{self.repository}/issues",
            params={"state": "all", "labels": REPORT_LABEL, "per_page": 100},
        )
        existing = next(
            (issue for issue in issues if issue.get("title") == title), None
        )
        created = existing is None
        if created:
            issue = self._call(
                "POST",
                f"https://api.github.com/repos/{self.repository}/issues",
                json={"title": title, "body": issue_body, "labels": [REPORT_LABEL]},
            )
        else:
            assert existing is not None
            issue = self._call(
                "PATCH",
                f"https://api.github.com/repos/{self.repository}/issues/{existing['number']}",
                json={"body": issue_body},
            )

        mutation = """
          mutation($project: ID!, $content: ID!) {
            addProjectV2ItemById(input: {projectId: $project, contentId: $content}) {
              item { id }
            }
          }
        """
        # Always attempt it so a retry repairs a prior issue-created/project-failed run.
        project_result = self._call(
            "POST",
            "https://api.github.com/graphql",
            json={
                "query": mutation,
                "variables": {
                    "project": self.project_id,
                    "content": issue["node_id"],
                },
            },
        )
        errors = project_result.get("errors", [])
        if errors and not all(
            "already exists" in str(error.get("message", "")).lower()
            for error in errors
        ):
            raise RuntimeError(f"GitHub Project insertion failed: {errors}")

        comments_url = (
            f"https://api.github.com/repos/{self.repository}/issues/"
            f"{issue['number']}/comments"
        )
        existing_comments = self._call("GET", comments_url, params={"per_page": 100})
        report_comments = {
            str(comment.get("body", "")).splitlines()[0]: comment
            for comment in existing_comments
            if str(comment.get("body", "")).startswith(
                "<!-- orbit-testnet-report-part:"
            )
        }
        expected_markers = {
            f"<!-- orbit-testnet-report-part:{part_number} -->"
            for part_number in range(2, len(parts) + 1)
        }
        for part_number, part in enumerate(parts[1:], start=2):
            marker = f"<!-- orbit-testnet-report-part:{part_number} -->"
            comment_body = f"{marker}\n{part}"
            existing_comment = report_comments.get(marker)
            if existing_comment:
                self._call(
                    "PATCH", str(existing_comment["url"]), json={"body": comment_body}
                )
            else:
                self._call("POST", comments_url, json={"body": comment_body})
        for marker, stale_comment in report_comments.items():
            if marker not in expected_markers:
                self._call("DELETE", str(stale_comment["url"]))

        current_labels = {item["name"] for item in issue.get("labels", [])}
        if AGENT_LABEL not in current_labels:
            self._call(
                "POST",
                f"https://api.github.com/repos/{self.repository}/issues/{issue['number']}/labels",
                json={"labels": [AGENT_LABEL]},
            )
        return str(issue["html_url"])


class TestnetDailyReporter:
    """Read yesterday's ledgers and idempotently publish their GitHub report."""

    def __init__(
        self,
        mongo_handler: Any,
        github: GitHubProjectClient,
        futures_client: Any = None,
    ) -> None:
        self.mongo_handler = mongo_handler
        self.github = github
        self.futures_client = futures_client

    def publish_date(self, report_date: date) -> str:
        start = datetime.combine(report_date, time.min, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        if self.futures_client is not None:
            PerformanceTracker(
                self.futures_client, self.mongo_handler, "testnet"
            ).sync_window(int(start.timestamp() * 1000), int(end.timestamp() * 1000))
        decisions = self.mongo_handler.get_trade_decisions(start, end, "testnet")
        income = self.mongo_handler.get_income_records(
            int(start.timestamp() * 1000), int(end.timestamp() * 1000), "testnet"
        )
        title = f"Orbit Testnet daily report: {report_date.isoformat()}"
        return self.github.publish(
            title, build_report_body(report_date, decisions, income)
        )

    def publish_week(self, week_start: date) -> str:
        """Publish a completed Monday-through-Sunday UTC reporting window."""
        start = datetime.combine(week_start, time.min, tzinfo=timezone.utc)
        end = start + timedelta(days=7)
        if self.futures_client is not None:
            PerformanceTracker(
                self.futures_client, self.mongo_handler, "testnet"
            ).sync_window(int(start.timestamp() * 1000), int(end.timestamp() * 1000))
        decisions = self.mongo_handler.get_trade_decisions(start, end, "testnet")
        income = self.mongo_handler.get_income_records(
            int(start.timestamp() * 1000), int(end.timestamp() * 1000), "testnet"
        )
        title = f"Orbit Testnet weekly report: {week_start.isoformat()}"
        return self.github.publish(
            title, build_weekly_report_body(week_start, decisions, income)
        )

    def run_forever(self, interval_seconds: int = 3600) -> None:
        last_published: Optional[date] = None
        last_week_published: Optional[date] = None
        while True:
            today = datetime.now(timezone.utc).date()
            yesterday = today - timedelta(days=1)
            if yesterday != last_published:
                try:
                    url = self.publish_date(yesterday)
                    logger.info("Published Testnet daily report: %s", url)
                    last_published = yesterday
                except Exception:
                    logger.exception("Failed to publish Testnet daily report")
            latest_completed_week = _latest_completed_week(today)
            if latest_completed_week != last_week_published:
                try:
                    url = self.publish_week(latest_completed_week)
                    logger.info("Published Testnet weekly report: %s", url)
                    last_week_published = latest_completed_week
                except Exception:
                    logger.exception("Failed to publish Testnet weekly report")
            time_module.sleep(interval_seconds)

    @classmethod
    def from_env(
        cls, mongo_handler: Any, futures_client: Any = None
    ) -> Optional["TestnetDailyReporter"]:
        if os.getenv("ORBIT_GITHUB_REPORTING_ENABLED", "false").lower() != "true":
            return None
        token = os.getenv("ORBIT_GITHUB_TOKEN", "").strip()
        repository = os.getenv("ORBIT_GITHUB_REPOSITORY", "ipankaj18/Orbit").strip()
        project_id = os.getenv("ORBIT_GITHUB_PROJECT_ID", "").strip()
        if not token or not project_id:
            raise RuntimeError(
                "GitHub reporting requires ORBIT_GITHUB_TOKEN and ORBIT_GITHUB_PROJECT_ID"
            )
        return cls(
            mongo_handler,
            GitHubProjectClient(token, repository, project_id),
            futures_client,
        )
