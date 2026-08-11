"""Governance report delivery tests (v0.8.0, Slack + SMTP delivery)."""

from unittest.mock import AsyncMock, MagicMock, call, patch

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
    from memex.config import EmailSMTPConfig
    from memex.graph.governance_report import deliver_email

    report = _sample_report()
    smtp_config = EmailSMTPConfig(
        host="smtp.example.com",
        port=587,
        user="bot@example.com",
        password="hunter2",
        to=["eng-team@example.com"],
    )
    with patch("memex.graph.governance_report.smtplib.SMTP") as mock_smtp_cls:
        mock_smtp = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_smtp

        result = await deliver_email(report, smtp_config)

    assert result is True
    mock_smtp.starttls.assert_called_once()
    mock_smtp.login.assert_called_once_with("bot@example.com", "hunter2")
    mock_smtp.sendmail.assert_called_once()
    # Ordering matters: login() must never precede starttls() (credentials
    # would go over the wire unencrypted — a real SMTP protocol violation).
    # Individual assert_called_once* calls above don't check relative order.
    assert mock_smtp.mock_calls[0] == call.starttls()
    assert mock_smtp.mock_calls[1] == call.login("bot@example.com", "hunter2")
    assert mock_smtp.mock_calls[2][0] == "sendmail"
    # A hanging/unresponsive SMTP server must not block report_task()'s
    # sequential per-repo loop indefinitely — smtplib.SMTP() needs a bounded
    # timeout (mirrors deliver_slack's httpx.AsyncClient(timeout=10.0)).
    _args, kwargs = mock_smtp_cls.call_args
    assert kwargs.get("timeout") is not None


@pytest.mark.asyncio
async def test_email_failure_returns_false_not_raise():
    from memex.config import EmailSMTPConfig
    from memex.graph.governance_report import deliver_email

    report = _sample_report()
    smtp_config = EmailSMTPConfig(
        host="smtp.example.com", port=587, user="x", password="x", to=["x@x.com"]
    )
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


@pytest.mark.asyncio
async def test_report_task_calls_slack_when_configured():
    from memex.graph.decay import DecayScheduler

    fake_repo = MagicMock(path="/fake/repo")
    with patch("memex.watcher.registry.get_active_repositories", return_value=[fake_repo]), patch(
        "memex.graph.governance_report.generate_report", new=AsyncMock(return_value=_sample_report())
    ), patch("memex.graph.governance_report.write_report"), patch(
        "memex.graph.governance_report.deliver_slack", new=AsyncMock(return_value=True)
    ) as mock_slack, patch("memex.config.get_config") as mock_get_config:
        mock_get_config.return_value.governance.slack_webhook = "https://hooks.slack.test/x"
        mock_get_config.return_value.governance.email_smtp = None

        scheduler = DecayScheduler()
        await scheduler.report_task()

    mock_slack.assert_called_once()


@pytest.mark.asyncio
async def test_report_task_calls_email_when_configured():
    from memex.graph.decay import DecayScheduler
    from memex.config import EmailSMTPConfig

    fake_repo = MagicMock(path="/fake/repo")
    fake_smtp = EmailSMTPConfig(host="smtp.x.com", user="a@x.com", password="p", to=["b@x.com"])
    with patch("memex.watcher.registry.get_active_repositories", return_value=[fake_repo]), patch(
        "memex.graph.governance_report.generate_report", new=AsyncMock(return_value=_sample_report())
    ), patch("memex.graph.governance_report.write_report"), patch(
        "memex.graph.governance_report.deliver_email", new=AsyncMock(return_value=True)
    ) as mock_email, patch("memex.config.get_config") as mock_get_config:
        mock_get_config.return_value.governance.slack_webhook = None
        mock_get_config.return_value.governance.email_smtp = fake_smtp

        scheduler = DecayScheduler()
        await scheduler.report_task()

    mock_email.assert_called_once()
    # Confirm the EmailSMTPConfig instance is passed through directly, not a dict.
    call_args = mock_email.call_args
    assert call_args.args[1] is fake_smtp or call_args.kwargs.get("smtp_config") is fake_smtp


@pytest.mark.asyncio
async def test_report_task_no_delivery_when_unconfigured():
    """No governance.slack_webhook or governance.email_smtp configured ->
    neither delivery function is called, write_report() alone runs, exactly
    v0.7.0's behavior."""
    from memex.graph.decay import DecayScheduler

    fake_repo = MagicMock(path="/fake/repo")
    with patch("memex.watcher.registry.get_active_repositories", return_value=[fake_repo]), patch(
        "memex.graph.governance_report.generate_report", new=AsyncMock(return_value=_sample_report())
    ), patch("memex.graph.governance_report.write_report") as mock_write, patch(
        "memex.graph.governance_report.deliver_slack", new=AsyncMock()
    ) as mock_slack, patch(
        "memex.graph.governance_report.deliver_email", new=AsyncMock()
    ) as mock_email, patch("memex.config.get_config") as mock_get_config:
        mock_get_config.return_value.governance.slack_webhook = None
        mock_get_config.return_value.governance.email_smtp = None

        scheduler = DecayScheduler()
        await scheduler.report_task()

    mock_write.assert_called_once()
    mock_slack.assert_not_called()
    mock_email.assert_not_called()
