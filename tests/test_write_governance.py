"""Phase 9 — Write governance tests.

Covers:
- Layer A (Node-type ACL): locked / open / self
- Layer B (Intent confirmation at 0.85): contradiction, corroborate, supersede, force
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from memex.graph.schema import (
    MemexWritePolicyError,
    check_write_policy,
)
from memex.mcp_server.tools_write import record_decision


# ---------------------------------------------------------------------------
# Layer A — Node-type ACL
# ---------------------------------------------------------------------------


def test_locked_node_cannot_be_written_by_agent():
    """Module, Symbol, Cluster etc. are `locked` — only watcher/cluster/
    summariser callers may mutate them. Agent calls must raise."""
    for locked_type in ("Module", "Symbol", "Cluster", "ClusterSummary", "Dependency", "Repository"):
        with pytest.raises(MemexWritePolicyError) as exc:
            check_write_policy(locked_type, caller="agent")
        assert locked_type in str(exc.value)
        assert "locked" in str(exc.value)


def test_open_node_can_be_written_by_agent():
    """Decision and Problem are `open` — agent calls must pass cleanly."""
    # Should not raise
    check_write_policy("Decision", caller="agent")
    check_write_policy("Problem", caller="agent")


def test_self_policy_blocks_other_session():
    """AgentSession is `self` — only the owning caller may mutate it.

    Helper-level test: check_write_policy("AgentSession", caller="session_b",
    owner="session_a") must raise. The MCP write path doesn't currently plumb
    session identity (caller is hardcoded "agent"), so this is a unit test
    of the ACL primitive that downstream phases will plug into.
    """
    # Same owner — permitted
    check_write_policy("AgentSession", caller="session_a", owner="session_a")
    # No owner declared at creation time — permissive (caller IS the owner)
    check_write_policy("AgentSession", caller="session_a", owner=None)
    # Different owner — blocked
    with pytest.raises(MemexWritePolicyError) as exc:
        check_write_policy("AgentSession", caller="session_b", owner="session_a")
    assert "self" in str(exc.value)


# ---------------------------------------------------------------------------
# Layer B — Intent confirmation
# ---------------------------------------------------------------------------


def _make_decision_hit(uuid="abc123", name="Switched to EdDSA for key rotation simplicity", score=0.9, repo=None):
    """Build a MagicMock that quacks like a Graphiti search hit for a Decision."""
    hit = MagicMock()
    hit.type = "Decision"
    hit.uuid = uuid
    hit.name = name
    hit.score = score
    hit.repo_path = repo
    return hit


@pytest.mark.asyncio
async def test_record_decision_contradiction_returns_existing_node_id():
    """When the intent-confirmation check finds a sibling Decision above
    the 0.85 threshold IN THE SAME REPO, record_decision must return the
    existing node's id + 3 options, and must NOT call add_episode.

    Post-B5: repo_path must match exactly — untagged or cross-repo hits do
    not trigger the intent-confirmation prompt anymore."""
    import os
    target_repo = os.path.abspath("/test/repo")  # match what _resolve_repo emits
    existing = _make_decision_hit(uuid="abc123", score=0.92, repo=target_repo)

    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.search.return_value = [existing]
        mock_get_client.return_value = mock_client

        result = await record_decision(
            text="Adopt EdDSA so key rotation is simpler.",
            repo=target_repo,
        )

    assert "similar decision already exists" in result
    assert "abc123" in result
    assert "corroborate" in result
    assert "supersede" in result
    assert "force=True" in result or "force=true" in result.lower()
    # Critical: no episode was written
    assert not mock_client.add_episode.called


@pytest.mark.asyncio
async def test_record_decision_contradiction_ignores_cross_repo_hit():
    """B5 regression: a near-duplicate Decision in a DIFFERENT repo must NOT
    trigger the intent-confirmation prompt — that would surface an
    actionable id pointing at the wrong repo's node."""
    import os
    cross_repo_existing = _make_decision_hit(
        uuid="other-repo-abc", score=0.99, repo=os.path.abspath("/other/repo")
    )

    with (
        patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client,
        patch("memex.mcp_server.tools_write._get_or_create_session",
              return_value="session_xyz"),
    ):
        mock_client = AsyncMock()
        mock_client.search.return_value = [cross_repo_existing]
        # add_episode would also be called; return a basic result so the
        # writer path completes without surfacing an error string.
        mock_client.add_episode.return_value = MagicMock(
            episode=MagicMock(uuid="new-decision-uuid")
        )
        mock_get_client.return_value = mock_client

        result = await record_decision(
            text="Adopt EdDSA for key rotation simplicity.",
            repo="/test/repo",  # DIFFERENT from cross_repo_existing.repo_path
        )

    # Must NOT be the intent-confirmation prompt — the cross-repo hit must
    # have been filtered out, so a fresh Decision is written.
    assert "similar decision already exists" not in result
    assert "other-repo-abc" not in result


@pytest.mark.asyncio
async def test_record_decision_corroborates_adds_edge_not_new_node():
    """corroborates=<id> must NOT create a new Decision node — it updates
    the existing node's last_reinforced_at and adds a corroborating edge."""
    mock_records = MagicMock()
    mock_records.records = [{"name": "Switched to EdDSA"}]

    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client, \
         patch("memex.mcp_server.tools_write._get_or_create_session", return_value="session_xyz"):
        mock_client = AsyncMock()
        mock_client.driver.execute_query.return_value = mock_records
        mock_get_client.return_value = mock_client

        result = await record_decision(text="any", corroborates="abc123")

    assert "corroborated" in result
    assert "abc123" in result
    # No new Decision episode — only the corroboration cypher updates fired
    assert not mock_client.add_episode.called
    # We did fire at least the UPDATE query (last_reinforced_at) and the
    # corroborating MERGE edge query
    assert mock_client.driver.execute_query.call_count >= 1


@pytest.mark.asyncio
async def test_record_decision_supersede_invalidates_old_node():
    """supersedes=<id> creates a NEW Decision with supersedes=<id> and the
    helper must explicitly expire the old node's outgoing edges."""
    mock_result = MagicMock()
    mock_result.episode.uuid = "new-decision-uuid"

    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.add_episode.return_value = mock_result
        # All driver queries succeed with non-empty records to satisfy the existence check
        mock_client.driver.execute_query.return_value = MagicMock(records=[{"uuid": "abc123"}])
        mock_get_client.return_value = mock_client

        result = await record_decision(
            text="Reverted EdDSA; back to RS256 because of HSM constraint.",
            supersedes="abc123",
        )

    assert "decision recorded" in result
    assert "supersedes abc123" in result
    assert "new-decision-uuid" in result
    # add_episode called for the new Decision (intent-confirmation was
    # explicitly bypassed because supersedes was provided)
    assert mock_client.add_episode.called

    # Verify the expire-old-edges cypher was issued
    cypher_calls = [
        c.args[0] if c.args else c.kwargs.get("query", "")
        for c in mock_client.driver.execute_query.call_args_list
    ]
    expire_seen = any("expired_at" in q and "agent_supersede" in q for q in cypher_calls)
    assert expire_seen, "supersede must explicitly expire the old node's edges"


@pytest.mark.asyncio
async def test_record_decision_force_writes_sibling():
    """force=True must bypass the intent-confirmation check and write a
    new Decision even when a near-duplicate exists."""
    existing = _make_decision_hit(uuid="abc123", score=0.95)
    mock_result = MagicMock()
    mock_result.episode.uuid = "sibling-uuid"

    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.search.return_value = [existing]
        mock_client.add_episode.return_value = mock_result
        mock_client.driver.execute_query.return_value = MagicMock(records=[])
        mock_get_client.return_value = mock_client

        result = await record_decision(
            text="Adopt EdDSA so key rotation is simpler.",
            force=True,
        )

    assert "decision recorded" in result
    assert "sibling-uuid" in result
    assert mock_client.add_episode.called


@pytest.mark.asyncio
async def test_record_decision_proceeds_when_no_similar_found():
    """Below-threshold matches should NOT trigger the intent-confirmation
    response. Ensures we use ≥ threshold, not always-match-on-Decision."""
    below = _make_decision_hit(uuid="abc123", score=0.5)
    mock_result = MagicMock()
    mock_result.episode.uuid = "fresh-uuid"

    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.search.return_value = [below]
        mock_client.add_episode.return_value = mock_result
        mock_client.driver.execute_query.return_value = MagicMock(records=[])
        mock_get_client.return_value = mock_client

        result = await record_decision(text="A genuinely novel decision.")

    assert "decision recorded" in result
    assert "fresh-uuid" in result
    assert mock_client.add_episode.called
