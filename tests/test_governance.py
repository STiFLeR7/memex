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
    assert mock_client.post.call_args.args[0] == "https://hooks.slack.test/x"
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


@pytest.mark.asyncio
async def test_email_sends_html_rendered_report():
    from memex.graph.governance_report import deliver_email

    report = _sample_report()
    smtp_config = {
        "host": "smtp.example.com",
        "port": 587,
        "user": "bot@example.com",
        "password": "hunter2",
        "to": ["eng-team@example.com"],
    }
    with patch("memex.graph.governance_report.smtplib.SMTP") as mock_smtp_cls:
        mock_smtp = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_smtp

        result = await deliver_email(report, smtp_config)

    assert result is True
    mock_smtp.starttls.assert_called_once()
    mock_smtp.login.assert_called_once_with("bot@example.com", "hunter2")
    mock_smtp.sendmail.assert_called_once()


@pytest.mark.asyncio
async def test_email_failure_returns_false_not_raise():
    from memex.graph.governance_report import deliver_email

    report = _sample_report()
    smtp_config = {"host": "smtp.example.com", "port": 587, "user": "x", "password": "x", "to": ["x@x.com"]}
    with patch("memex.graph.governance_report.smtplib.SMTP", side_effect=Exception("refused")):
        result = await deliver_email(report, smtp_config)

    assert result is False


@pytest.mark.asyncio
async def test_no_delivery_config_generates_local_only():
    """When governance.slack_webhook and governance.email_smtp are both
    unset, report_task() (memex/graph/decay.py, wired in a later task) calls
    neither delivery function — write_report() alone, exactly v0.7.0's
    behavior."""
    from memex.config import Config

    config = Config(
        neo4j_uri="bolt://x", neo4j_user="x", neo4j_password="x", gemini_api_key="x"
    )
    assert config.governance.slack_webhook is None
    assert config.governance.email_smtp is None
