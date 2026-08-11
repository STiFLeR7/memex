"""Governance report delivery tests (v0.8.0, Slack + SMTP delivery)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memex.graph.governance_report import GovernanceReport, deliver_slack


def _sample_report() -> GovernanceReport:
    return GovernanceReport(
        repo_path="/fake/repo",
        period_days=7,
        generated_at="2026-08-10T00:00:00+00:00",
        telemetry={},
        period_telemetry={},
        confidence_distribution={"high": 1, "mid": 0, "stale": 0},
        unvalidated_decisions=[],
        modules_touched=[],
    )


@pytest.mark.asyncio
async def test_slack_webhook_posts_markdown():
    report = _sample_report()
    with patch("memex.graph.governance_report.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))
        mock_client_cls.return_value = mock_client

        result = await deliver_slack(report, webhook_url="https://hooks.slack.test/x")

    assert result is True
    posted_body = mock_client.post.call_args.kwargs["json"]
    assert "text" in posted_body
    assert "Governance Report" in posted_body["text"]


@pytest.mark.asyncio
async def test_slack_webhook_failure_returns_false_not_raise():
    report = _sample_report()
    with patch("memex.graph.governance_report.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(side_effect=Exception("network down"))
        mock_client_cls.return_value = mock_client

        result = await deliver_slack(report, webhook_url="https://hooks.slack.test/x")

    assert result is False


def test_governance_config_defaults_to_no_delivery():
    from memex.config import Config

    config = Config(
        neo4j_uri="bolt://x", neo4j_user="x", neo4j_password="x", gemini_api_key="x"
    )
    assert config.governance.slack_webhook is None
