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
    body = "\n".join(lines)
    if len(body) > 65_000:
        raise ValueError("Daily report exceeds GitHub's issue body limit")
    return body


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
                json={"title": title, "body": body, "labels": [REPORT_LABEL]},
            )
        else:
            assert existing is not None
            issue = self._call(
                "PATCH",
                f"https://api.github.com/repos/{self.repository}/issues/{existing['number']}",
                json={"body": body},
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
            PerformanceTracker(self.futures_client, self.mongo_handler, "testnet").sync(
                int(start.timestamp() * 1000)
            )
        decisions = self.mongo_handler.get_trade_decisions(start, end, "testnet")
        income = self.mongo_handler.get_income_records(
            int(start.timestamp() * 1000), int(end.timestamp() * 1000), "testnet"
        )
        title = f"Orbit Testnet daily report: {report_date.isoformat()}"
        return self.github.publish(
            title, build_report_body(report_date, decisions, income)
        )

    def run_forever(self, interval_seconds: int = 3600) -> None:
        last_published: Optional[date] = None
        while True:
            yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
            if yesterday != last_published:
                try:
                    url = self.publish_date(yesterday)
                    logger.info("Published Testnet daily report: %s", url)
                    last_published = yesterday
                except Exception:
                    logger.exception("Failed to publish Testnet daily report")
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
