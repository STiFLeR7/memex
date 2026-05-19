"""Tests for the ``memex review`` terminal UI (Phase 8 / ARCHITECTURE §9).

Key invariants:
    - Decisions are presented lowest-computed-confidence first.
    - The keystroke prompt never shows the numeric confidence (ACL 2025 bias).
    - 'y' sets validated=True + validated_at.
    - 'n' soft-excludes the decision (excluded=True), does NOT destructive-delete.
    - 'e' opens the editor and round-trips the new text.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memex import cli_review


def _fake_record(d: dict[str, Any]) -> MagicMock:
    """Build a fake neo4j record whose ``.data()`` returns the dict."""
    rec = MagicMock()
    rec.data.return_value = dict(d)
    rec.__getitem__.side_effect = lambda k, _d=d: _d.get(k)
    return rec


def _make_decisions_result(rows: list[dict[str, Any]]) -> MagicMock:
    res = MagicMock()
    res.records = [_fake_record(r) for r in rows]
    return res


def _make_client_with_fetch(rows: list[dict[str, Any]]):
    """Return (mock_client, list_of_observed_calls) where:
        - the first execute_query returns the fetched rows
        - subsequent calls are captured in update_calls."""
    mock_client = AsyncMock()
    mock_driver = AsyncMock()
    mock_client.driver = mock_driver

    select_res = _make_decisions_result(rows)
    update_calls: list[tuple[str, dict]] = []

    async def fake_execute_query(query, params=None, **kw):
        if "MATCH (d:Entity)" in query and "validated" in query and "RETURN" in query and "modules" in query:
            return select_res
        update_calls.append((query, params or {}))
        update_res = MagicMock()
        update_res.records = []
        return update_res

    mock_driver.execute_query.side_effect = fake_execute_query
    return mock_client, update_calls


@pytest.mark.asyncio
async def test_review_command_empty_queue_exits_gracefully():
    """No pending decisions → friendly message, no exception."""
    mock_client, _ = _make_client_with_fetch([])

    with patch("memex.cli_review.get_graph_client", return_value=mock_client):
        # Must complete without raising.
        await cli_review.run_review_command(".")


@pytest.mark.asyncio
async def test_review_command_orders_lowest_confidence_first(monkeypatch):
    """Most-decayed decision must be presented first (Prodigy / Label Studio
    active-learning convention)."""
    fresh = {
        "id": "fresh-id",
        "text": "fresh decision",
        "rationale": "",
        "source_commit": "aaaa1111",
        "source": "watcher",
        "corroborated": False,
        "validated": False,
        "base_confidence": 0.6,
        "last_reinforced_at": datetime.now(timezone.utc),
        "created_at": datetime.now(timezone.utc),
        "modules": [],
    }
    stale = {
        "id": "stale-id",
        "text": "stale decision",
        "rationale": "",
        "source_commit": "bbbb2222",
        "source": "watcher",
        "corroborated": False,
        "validated": False,
        "base_confidence": 0.6,
        "last_reinforced_at": datetime.now(timezone.utc) - timedelta(days=40),
        "created_at": datetime.now(timezone.utc) - timedelta(days=40),
        "modules": [],
    }
    mock_client, _ = _make_client_with_fetch([fresh, stale])

    rendered_order: list[str] = []

    def fake_render(console, idx, total, decision):
        rendered_order.append(decision["id"])

    async def fake_prompt(console):
        return "q"  # quit immediately after first card

    with patch("memex.cli_review.get_graph_client", return_value=mock_client):
        with patch("memex.cli_review._render_panel", side_effect=fake_render):
            with patch("memex.cli_review._prompt", side_effect=fake_prompt):
                await cli_review.run_review_command(".")

    assert rendered_order, "expected at least one card to be rendered"
    # Stale should be first because its computed_confidence is lower.
    assert rendered_order[0] == "stale-id"


@pytest.mark.asyncio
async def test_review_command_does_not_show_confidence_in_prompt():
    """ACL 2025 anchoring finding: showing the numeric confidence biases
    annotators 81-87% toward 'y'. The prompt string must not include any
    confidence number or the word 'confidence'."""
    captured: list[str] = []

    def fake_ask(*args, **kwargs):
        # Prompt.ask(message, choices=..., ...) — first positional is the message.
        captured.append(args[0] if args else kwargs.get("prompt", ""))
        return "q"

    with patch("memex.cli_review.Prompt.ask", side_effect=fake_ask):
        result = await cli_review._prompt(MagicMock())

    assert result == "q"
    assert captured, "expected Prompt.ask to be called"
    prompt_text = captured[0].lower()
    assert "confidence" not in prompt_text
    # No bare floats like "0.60" should appear in the keystroke prompt.
    assert "0." not in prompt_text


@pytest.mark.asyncio
async def test_review_validate_sets_validated_true_and_validated_at():
    """Pressing 'y' must issue a Cypher UPDATE that sets both ``validated``
    and ``validated_at`` on the decision."""
    decision = {
        "id": "dec-1",
        "text": "test decision",
        "rationale": "",
        "source_commit": "sha1",
        "source": "watcher",
        "corroborated": False,
        "validated": False,
        "base_confidence": 0.6,
        "last_reinforced_at": datetime.now(timezone.utc),
        "created_at": datetime.now(timezone.utc),
        "modules": [],
    }
    mock_client, update_calls = _make_client_with_fetch([decision])

    # First prompt → 'y' (validate), no second card since only one decision.
    prompt_iter = iter(["y"])

    async def fake_prompt(console):
        return next(prompt_iter)

    with patch("memex.cli_review.get_graph_client", return_value=mock_client):
        with patch("memex.cli_review._prompt", side_effect=fake_prompt):
            await cli_review.run_review_command(".")

    assert update_calls, "expected an UPDATE Cypher to fire"
    update_query, params = update_calls[0]
    assert "d.validated" in update_query and "true" in update_query.lower()
    assert "d.validated_at" in update_query
    assert "d.last_reinforced_at" in update_query
    assert params["id"] == "dec-1"


@pytest.mark.asyncio
async def test_review_delete_excludes_decision():
    """Pressing 'n' must soft-delete (set excluded=True), NOT DETACH DELETE."""
    decision = {
        "id": "dec-2",
        "text": "delete me",
        "rationale": "",
        "source_commit": "sha2",
        "source": "watcher",
        "corroborated": False,
        "validated": False,
        "base_confidence": 0.6,
        "last_reinforced_at": datetime.now(timezone.utc),
        "created_at": datetime.now(timezone.utc),
        "modules": [],
    }
    mock_client, update_calls = _make_client_with_fetch([decision])

    prompt_iter = iter(["n"])

    async def fake_prompt(console):
        return next(prompt_iter)

    with patch("memex.cli_review.get_graph_client", return_value=mock_client):
        with patch("memex.cli_review._prompt", side_effect=fake_prompt):
            await cli_review.run_review_command(".")

    assert update_calls, "expected an UPDATE Cypher to fire"
    update_query, params = update_calls[0]
    assert "d.excluded" in update_query
    assert "DETACH DELETE" not in update_query.upper(), (
        "soft-delete only — preserve audit trail"
    )
    assert params["id"] == "dec-2"


@pytest.mark.asyncio
async def test_review_edit_opens_editor_and_updates_text():
    """Pressing 'e' opens ``$EDITOR`` on the decision text and writes the new
    text back via a Cypher UPDATE."""
    decision = {
        "id": "dec-3",
        "text": "original text",
        "rationale": "",
        "source_commit": "sha3",
        "source": "watcher",
        "corroborated": False,
        "validated": False,
        "base_confidence": 0.6,
        "last_reinforced_at": datetime.now(timezone.utc),
        "created_at": datetime.now(timezone.utc),
        "modules": [],
    }
    mock_client, update_calls = _make_client_with_fetch([decision])

    prompt_iter = iter(["e"])

    async def fake_prompt(console):
        return next(prompt_iter)

    editor_calls: list[str] = []

    def fake_editor(initial):
        editor_calls.append(initial)
        return "edited text"

    with patch("memex.cli_review.get_graph_client", return_value=mock_client):
        with patch("memex.cli_review._prompt", side_effect=fake_prompt):
            with patch("memex.cli_review._open_editor", side_effect=fake_editor):
                await cli_review.run_review_command(".")

    assert editor_calls == ["original text"]
    assert update_calls, "expected text-update Cypher to fire"
    update_query, params = update_calls[0]
    assert "d.name" in update_query
    assert params["text"] == "edited text"
    assert params["id"] == "dec-3"


@pytest.mark.asyncio
async def test_review_skip_does_not_mutate():
    """Pressing 's' must do nothing in Neo4j (no UPDATE)."""
    decision = {
        "id": "dec-4",
        "text": "leave me alone",
        "rationale": "",
        "source_commit": "sha4",
        "source": "watcher",
        "corroborated": False,
        "validated": False,
        "base_confidence": 0.6,
        "last_reinforced_at": datetime.now(timezone.utc),
        "created_at": datetime.now(timezone.utc),
        "modules": [],
    }
    mock_client, update_calls = _make_client_with_fetch([decision])

    prompt_iter = iter(["s"])

    async def fake_prompt(console):
        return next(prompt_iter)

    with patch("memex.cli_review.get_graph_client", return_value=mock_client):
        with patch("memex.cli_review._prompt", side_effect=fake_prompt):
            await cli_review.run_review_command(".")

    assert not update_calls, "skip must not issue any UPDATE Cypher"


@pytest.mark.asyncio
async def test_review_quit_stops_iteration():
    """Pressing 'q' on card 1 must skip card 2 entirely."""
    rows = [
        {
            "id": f"dec-{i}",
            "text": f"d{i}",
            "rationale": "",
            "source_commit": "sha",
            "source": "watcher",
            "corroborated": False,
            "validated": False,
            "base_confidence": 0.6,
            "last_reinforced_at": datetime.now(timezone.utc) - timedelta(days=i * 5),
            "created_at": datetime.now(timezone.utc),
            "modules": [],
        }
        for i in range(3)
    ]
    mock_client, update_calls = _make_client_with_fetch(rows)

    rendered: list[str] = []

    def fake_render(console, idx, total, decision):
        rendered.append(decision["id"])

    async def fake_prompt(console):
        return "q"

    with patch("memex.cli_review.get_graph_client", return_value=mock_client):
        with patch("memex.cli_review._render_panel", side_effect=fake_render):
            with patch("memex.cli_review._prompt", side_effect=fake_prompt):
                await cli_review.run_review_command(".")

    # Only the first card should have been rendered before 'q' fired.
    assert len(rendered) == 1
    assert not update_calls
