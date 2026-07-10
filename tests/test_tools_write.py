import pytest
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch, MagicMock
from memex.mcp_server.tools_write import (
    record_decision,
    record_problem,
    resolve_problem,
    invalidate_edge,
    _resolve_project,
    _get_or_create_session,
)

@pytest.mark.asyncio
async def test_record_decision_creates_node():
    mock_result = MagicMock()
    mock_result.episode.uuid = "uuid-123"
    
    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.add_episode.return_value = mock_result
        mock_get_client.return_value = mock_client
        
        result = await record_decision(
            text="Use RS256 for JWT tokens.",
            rationale="Security compliance."
        )
        
        assert "decision recorded" in result
        assert "uuid-123" in result
        assert mock_client.add_episode.called

@pytest.mark.asyncio
async def test_record_decision_rejects_short_text():
    result = await record_decision(text="too short")
    assert "decision text too short" in result

@pytest.mark.asyncio
async def test_record_decision_missing_module_still_creates():
    mock_result = MagicMock()
    mock_result.episode.uuid = "uuid-456"
    
    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.add_episode.return_value = mock_result
        mock_get_client.return_value = mock_client
        
        result = await record_decision(
            text="Switched to EdDSA for key rotation."
        )
        
        assert "decision recorded" in result
        assert "uuid-456" in result
        # Should be called once for the decision, no extra calls for linking
        assert mock_client.add_episode.call_count == 1

@pytest.mark.asyncio
async def test_record_problem_creates_node():
    mock_result = MagicMock()
    mock_result.episode.uuid = "prob-123"
    
    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.search.return_value = [] # No duplicates
        mock_client.add_episode.return_value = mock_result
        mock_get_client.return_value = mock_client
        
        result = await record_problem(text="Memory leak in watcher daemon.")
        assert "problem recorded [medium]" in result
        assert "prob-123" in result

@pytest.mark.asyncio
async def test_record_problem_invalid_severity_coerced():
    mock_result = MagicMock()
    mock_result.episode.uuid = "prob-456"
    
    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.search.return_value = []
        mock_client.add_episode.return_value = mock_result
        mock_get_client.return_value = mock_client
        
        result = await record_problem(text="Broken git hook installer.", severity="super-critical")
        assert "coerced to medium" in result
        assert "[medium]" in result

@pytest.mark.asyncio
async def test_record_problem_duplicate_detection():
    """Post-B6: same-repo near-duplicate Problem returns the dedup string.
    The mock's repo_path must match the call's repo for the strict check."""
    import os
    target_repo = os.path.abspath("/tmp/repo")

    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_duplicate = MagicMock()
        mock_duplicate.type = "Problem"
        mock_duplicate.score = 0.95
        mock_duplicate.name = "Watcher memory leak"
        mock_duplicate.uuid = "old-prob-999"
        mock_duplicate.repo_path = target_repo

        mock_client.search.return_value = [mock_duplicate]
        mock_get_client.return_value = mock_client

        result = await record_problem(
            text="Memory leak in watcher daemon.",
            repo=target_repo,
        )
        assert "similar problem already recorded" in result
        assert "old-prob-999" in result
        assert not mock_client.add_episode.called


@pytest.mark.asyncio
async def test_record_problem_dedup_ignores_cross_repo_hit():
    """B6 regression: a near-duplicate Problem in a DIFFERENT repo must NOT
    trigger the dedup return — that would surface an actionable id pointing
    at the wrong repo's node. Twin of the B5 fix on record_decision."""
    import os
    target_repo = os.path.abspath("/test/repo")
    cross_repo = os.path.abspath("/other/repo")

    with (
        patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client,
        patch("memex.mcp_server.tools_write._get_or_create_session",
              return_value="session_xyz"),
    ):
        mock_client = AsyncMock()
        cross_repo_dup = MagicMock()
        cross_repo_dup.type = "Problem"
        cross_repo_dup.score = 0.99
        cross_repo_dup.name = "Watcher memory leak"
        cross_repo_dup.uuid = "other-repo-prob"
        cross_repo_dup.repo_path = cross_repo

        mock_client.search.return_value = [cross_repo_dup]
        mock_client.add_episode.return_value = MagicMock(
            episode=MagicMock(uuid="new-prob-uuid")
        )
        mock_get_client.return_value = mock_client

        result = await record_problem(
            text="Memory leak in watcher daemon.",
            repo=target_repo,
        )

    # Must NOT be the dedup string — cross-repo hit must be filtered out.
    assert "similar problem already recorded" not in result
    assert "other-repo-prob" not in result

@pytest.mark.asyncio
async def test_resolve_problem_closes_node():
    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        # Mock finding the problem
        mock_res = MagicMock()
        mock_res.records = [{"text": "Broken auth", "resolved_at": None, "repo_path": "/tmp/repo"}]
        mock_client.driver.execute_query.side_effect = [mock_res, MagicMock()]
        
        mock_get_client.return_value = mock_client
        
        with patch("memex.mcp_server.tools_write._get_or_create_session", return_value="sess-1"):
            result = await resolve_problem(problem_id="prob-1", resolution_text="Fixed the bug in auth.")
            assert "problem resolved" in result
            assert "Broken auth" in result
            assert mock_client.add_episode.called

@pytest.mark.asyncio
async def test_resolve_problem_not_found_returns_message():
    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.driver.execute_query.return_value = MagicMock(records=[])
        mock_get_client.return_value = mock_client
        
        result = await resolve_problem(problem_id="missing", resolution_text="fixed it anyway")
        assert "not found" in result

@pytest.mark.asyncio
async def test_resolve_problem_already_resolved_returns_message():
    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_res = MagicMock()
        mock_res.records = [{"text": "P1", "resolved_at": datetime.now(), "resolution_summary": "Done", "repo_path": "/tmp/repo"}]
        mock_client.driver.execute_query.return_value = mock_res
        mock_get_client.return_value = mock_client
        
        result = await resolve_problem(problem_id="p1", resolution_text="resolving again")
        assert "already resolved" in result

@pytest.mark.asyncio
async def test_invalidate_edge_sets_valid_until():
    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_res = MagicMock()
        mock_res.records = [{"source": "M1", "target": "S1", "edge_type": "EXPORTS", "valid_until": None, "repo_path": "/tmp/repo"}]
        mock_client.driver.execute_query.side_effect = [mock_res, MagicMock()]
        mock_get_client.return_value = mock_client
        
        result = await invalidate_edge(edge_id="edge-123", reason="Symbol moved to another file.")
        assert "edge invalidated" in result
        assert "M1" in result
        assert "S1" in result
        assert mock_client.driver.execute_query.call_count == 2

@pytest.mark.asyncio
async def test_invalidate_edge_not_found_returns_message():
    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.driver.execute_query.return_value = MagicMock(records=[])
        mock_get_client.return_value = mock_client
        
        result = await invalidate_edge(edge_id="missing", reason="invalid")
        assert "not found" in result

@pytest.mark.asyncio
async def test_invalidate_edge_already_invalidated_returns_message():
    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_res = MagicMock()
        mock_res.records = [{"valid_until": datetime.now(), "old_reason": "Outdated"}]
        mock_client.driver.execute_query.return_value = mock_res
        mock_get_client.return_value = mock_client
        
        result = await invalidate_edge(edge_id="e1", reason="new reason")
        assert "already invalidated" in result

@pytest.mark.asyncio
async def test_record_problem_concurrent_calls_no_duplicate():
    """
    Use asyncio.gather to fire two identical record_problem calls simultaneously,
    assert only one Problem node was created.
    Post-B6: the simulated duplicate must carry the call's repo_path so the
    strict-match dedup check fires.
    """
    import os
    target_repo = os.path.abspath("/tmp/repo")

    mock_result = MagicMock()
    mock_result.episode.uuid = "prob-unique"

    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        # Mock search to return nothing initially; the second call sees the
        # duplicate the first one is racing to write.
        call_count = 0
        async def mock_search(*args, **kwargs):
            nonlocal call_count
            if call_count == 0:
                call_count += 1
                return []
            else:
                dup = MagicMock()
                dup.type = "Problem"
                dup.score = 0.99
                dup.name = "Duplicate issue"
                dup.uuid = "prob-unique"
                dup.repo_path = target_repo  # B6: must match for dedup
                return [dup]

        mock_client.search.side_effect = mock_search
        mock_client.add_episode.return_value = mock_result
        mock_get_client.return_value = mock_client

        results = await asyncio.gather(
            record_problem(text="Concurrent issue detection.", repo=target_repo),
            record_problem(text="Concurrent issue detection.", repo=target_repo),
        )

        success = [r for r in results if "problem recorded" in r]
        duplicates = [r for r in results if "similar problem already recorded" in r]

        assert len(success) == 1
        assert len(duplicates) == 1
        assert mock_client.add_episode.call_count == 1


# ---------------------------------------------------------------------------
# Phase 00 Plan 02 — project_id write-path wiring (NET-01/NET-02/NET-03)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_project_is_awaitable_and_returns_resolver_value():
    """_resolve_project() must be await-able and simply return whatever the
    sync `resolve_project_id` resolver returns, run via asyncio.to_thread."""
    with patch(
        "memex.mcp_server.tools_write.resolve_project_id",
        return_value="github.com/acme/widgets",
    ) as mock_resolve:
        result = await _resolve_project("/abs/repo")
        assert result == "github.com/acme/widgets"
        mock_resolve.assert_called_once_with("/abs/repo")


@pytest.mark.asyncio
async def test_resolve_project_returns_none_when_unresolvable():
    with patch("memex.mcp_server.tools_write.resolve_project_id", return_value=None):
        result = await _resolve_project("/abs/repo")
        assert result is None


@pytest.mark.asyncio
async def test_record_decision_threads_project_id_when_resolvable():
    mock_result = MagicMock()
    mock_result.episode.uuid = "uuid-project-1"

    with (
        patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client,
        patch(
            "memex.mcp_server.tools_write.resolve_project_id",
            return_value="github.com/acme/widgets",
        ),
    ):
        mock_client = AsyncMock()
        mock_client.add_episode.return_value = mock_result
        mock_get_client.return_value = mock_client

        result = await record_decision(text="Use RS256 for JWT tokens.")
        assert "decision recorded" in result

        # Find the SET call that updates the main decision node.
        found = False
        for call in mock_client.driver.execute_query.call_args_list:
            cypher = call.args[0] if call.args else call.kwargs.get("query", "")
            params = call.kwargs.get("params", {})
            if "n.project_id = $project" in cypher:
                found = True
                assert params.get("project") == "github.com/acme/widgets"
        assert found, "expected a SET n.project_id = $project clause"


@pytest.mark.asyncio
async def test_record_decision_omits_project_id_when_unresolvable():
    mock_result = MagicMock()
    mock_result.episode.uuid = "uuid-project-2"

    with (
        patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client,
        patch("memex.mcp_server.tools_write.resolve_project_id", return_value=None),
    ):
        mock_client = AsyncMock()
        mock_client.add_episode.return_value = mock_result
        mock_get_client.return_value = mock_client

        result = await record_decision(text="Switched to EdDSA for key rotation.")
        assert "decision recorded" in result

        for call in mock_client.driver.execute_query.call_args_list:
            cypher = call.args[0] if call.args else call.kwargs.get("query", "")
            params = call.kwargs.get("params", {})
            assert "n.project_id" not in cypher
            assert "project" not in params


@pytest.mark.asyncio
async def test_record_decision_with_module_link_threads_project_id():
    mock_result = MagicMock()
    mock_result.episode.uuid = "uuid-project-3"
    link_result = MagicMock()
    link_result.episode.uuid = "uuid-link-3"

    with (
        patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client,
        patch(
            "memex.mcp_server.tools_write.resolve_project_id",
            return_value="github.com/acme/widgets",
        ),
    ):
        mock_client = AsyncMock()
        mock_client.add_episode.side_effect = [mock_result, link_result]
        mock_get_client.return_value = mock_client

        result = await record_decision(
            text="Use RS256 for JWT tokens.", module="auth/jwt.py"
        )
        assert "decision recorded" in result

        # The module-link episode's update Cypher (keyed on link_result uuid)
        # must also carry the conditional project_id clause.
        link_call_found = False
        for call in mock_client.driver.execute_query.call_args_list:
            cypher = call.args[0] if call.args else call.kwargs.get("query", "")
            params = call.kwargs.get("params", {})
            if params.get("id") == "uuid-link-3":
                link_call_found = True
                assert "n.project_id = $project" in cypher
                assert params.get("project") == "github.com/acme/widgets"
        assert link_call_found


@pytest.mark.asyncio
async def test_record_problem_threads_project_id_when_resolvable():
    mock_result = MagicMock()
    mock_result.episode.uuid = "prob-project-1"

    with (
        patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client,
        patch(
            "memex.mcp_server.tools_write.resolve_project_id",
            return_value="github.com/acme/widgets",
        ),
    ):
        mock_client = AsyncMock()
        mock_client.search.return_value = []
        mock_client.add_episode.return_value = mock_result
        mock_get_client.return_value = mock_client

        result = await record_problem(text="Memory leak in watcher daemon.")
        assert "problem recorded" in result

        found = False
        for call in mock_client.driver.execute_query.call_args_list:
            cypher = call.args[0] if call.args else call.kwargs.get("query", "")
            params = call.kwargs.get("params", {})
            if "n.project_id = $project" in cypher:
                found = True
                assert params.get("project") == "github.com/acme/widgets"
        assert found


@pytest.mark.asyncio
async def test_record_problem_with_module_link_threads_project_id():
    mock_result = MagicMock()
    mock_result.episode.uuid = "prob-project-2"
    link_result = MagicMock()
    link_result.episode.uuid = "prob-link-2"

    with (
        patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client,
        patch(
            "memex.mcp_server.tools_write.resolve_project_id",
            return_value="github.com/acme/widgets",
        ),
    ):
        mock_client = AsyncMock()
        mock_client.search.return_value = []
        mock_client.add_episode.side_effect = [mock_result, link_result]
        mock_get_client.return_value = mock_client

        result = await record_problem(
            text="Memory leak in watcher daemon.", module="watcher/daemon.py"
        )
        assert "problem recorded" in result

        link_call_found = False
        for call in mock_client.driver.execute_query.call_args_list:
            cypher = call.args[0] if call.args else call.kwargs.get("query", "")
            params = call.kwargs.get("params", {})
            if params.get("id") == "prob-link-2":
                link_call_found = True
                assert "n.project_id = $project" in cypher
                assert params.get("project") == "github.com/acme/widgets"
        assert link_call_found


@pytest.mark.asyncio
async def test_resolve_problem_threads_project_id_when_resolvable():
    with (
        patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client,
        patch(
            "memex.mcp_server.tools_write.resolve_project_id",
            return_value="github.com/acme/widgets",
        ),
        patch(
            "memex.mcp_server.tools_write._get_or_create_session",
            return_value="sess-1",
        ),
    ):
        mock_client = AsyncMock()
        mock_res = MagicMock()
        mock_res.records = [
            {"text": "Broken auth", "resolved_at": None, "repo_path": "/tmp/repo"}
        ]
        mock_client.driver.execute_query.side_effect = [mock_res, MagicMock()]
        mock_get_client.return_value = mock_client

        result = await resolve_problem(
            problem_id="prob-1", resolution_text="Fixed the bug in auth."
        )
        assert "problem resolved" in result

        # The second execute_query call is the update_query that closes the
        # problem — it must now carry the conditional project_id clause.
        update_call = mock_client.driver.execute_query.call_args_list[1]
        cypher = update_call.args[0] if update_call.args else update_call.kwargs.get("query", "")
        params = update_call.kwargs.get("params", {})
        assert "p.project_id = $project" in cypher
        assert params.get("project") == "github.com/acme/widgets"


@pytest.mark.asyncio
async def test_resolve_problem_omits_project_id_when_unresolvable():
    with (
        patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client,
        patch("memex.mcp_server.tools_write.resolve_project_id", return_value=None),
        patch(
            "memex.mcp_server.tools_write._get_or_create_session",
            return_value="sess-1",
        ),
    ):
        mock_client = AsyncMock()
        mock_res = MagicMock()
        mock_res.records = [
            {"text": "Broken auth", "resolved_at": None, "repo_path": "/tmp/repo"}
        ]
        mock_client.driver.execute_query.side_effect = [mock_res, MagicMock()]
        mock_get_client.return_value = mock_client

        result = await resolve_problem(
            problem_id="prob-1", resolution_text="Fixed the bug in auth."
        )
        assert "problem resolved" in result

        update_call = mock_client.driver.execute_query.call_args_list[1]
        cypher = update_call.args[0] if update_call.args else update_call.kwargs.get("query", "")
        params = update_call.kwargs.get("params", {})
        assert "p.project_id" not in cypher
        assert "project" not in params


@pytest.mark.asyncio
async def test_get_or_create_session_threads_project_id_when_resolvable():
    import memex.mcp_server.tools_write as tw

    # Phase 01 — session caching is now keyed per (repo, agent, principal) in
    # `_session_cache` rather than a single module-global; clear it so this
    # test's key is guaranteed to miss the cache and exercise a real write.
    tw._session_cache.clear()
    try:
        with patch(
            "memex.mcp_server.tools_write.resolve_project_id",
            return_value="github.com/acme/widgets",
        ):
            mock_client = AsyncMock()
            mock_client.add_episode.return_value = MagicMock()

            await _get_or_create_session(mock_client, "/tmp/repo")

            found = False
            for call in mock_client.driver.execute_query.call_args_list:
                cypher = call.args[0] if call.args else call.kwargs.get("query", "")
                params = call.kwargs.get("params", {})
                if "n.project_id = $project" in cypher:
                    found = True
                    assert params.get("project") == "github.com/acme/widgets"
            assert found
    finally:
        tw._session_cache.clear()


@pytest.mark.asyncio
async def test_get_or_create_session_omits_project_id_when_unresolvable():
    import memex.mcp_server.tools_write as tw

    tw._session_cache.clear()
    try:
        with patch("memex.mcp_server.tools_write.resolve_project_id", return_value=None):
            mock_client = AsyncMock()
            mock_client.add_episode.return_value = MagicMock()

            await _get_or_create_session(mock_client, "/tmp/repo")

            for call in mock_client.driver.execute_query.call_args_list:
                cypher = call.args[0] if call.args else call.kwargs.get("query", "")
                params = call.kwargs.get("params", {})
                assert "n.project_id" not in cypher
                assert "project" not in params
    finally:
        tw._session_cache.clear()


@pytest.mark.asyncio
async def test_invalidate_edge_unchanged_no_project_id_clause():
    """invalidate_edge never sets repo_path today, so it must gain no
    project_id SET clause either (confirmed in 00-RESEARCH.md's Runtime
    State Inventory)."""
    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_res = MagicMock()
        mock_res.records = [
            {
                "source": "M1",
                "target": "S1",
                "edge_type": "EXPORTS",
                "valid_until": None,
                "repo_path": "/tmp/repo",
            }
        ]
        mock_client.driver.execute_query.side_effect = [mock_res, MagicMock()]
        mock_get_client.return_value = mock_client

        result = await invalidate_edge(edge_id="edge-123", reason="Symbol moved to another file.")
        assert "edge invalidated" in result

        for call in mock_client.driver.execute_query.call_args_list:
            cypher = call.args[0] if call.args else call.kwargs.get("query", "")
            assert "project_id" not in cypher


# ---------------------------------------------------------------------------
# Phase 01 Plan 01 — agent identity threading (NET-04/NET-07)
#
# These tests exercise `_get_or_create_session`'s real dict-keying logic
# directly (unmocked), rather than patching the function away wholesale —
# per 01-RESEARCH.md Pitfall 2, mocked tests elsewhere cannot catch a broken
# rekeying implementation.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_create_session_two_agents_get_distinct_sessions():
    """Two different agent identities against the same repo_path must NOT
    collapse into one shared AgentSession (NET-07)."""
    import memex.mcp_server.tools_write as tw

    tw._session_cache.clear()
    try:
        with patch("memex.mcp_server.tools_write.resolve_project_id", return_value=None):
            mock_client = AsyncMock()
            mock_client.add_episode.return_value = MagicMock()

            name_a = await _get_or_create_session(mock_client, "/tmp/repo", agent="claude-code")
            name_b = await _get_or_create_session(mock_client, "/tmp/repo", agent="gemini-cli")

            assert name_a != name_b
            assert mock_client.add_episode.call_count == 2
    finally:
        tw._session_cache.clear()


@pytest.mark.asyncio
async def test_get_or_create_session_same_tuple_is_cached():
    """The same (repo, agent, principal) tuple must reuse the cached session
    name on a second call instead of writing a duplicate AgentSession node
    (NET-07)."""
    import memex.mcp_server.tools_write as tw

    tw._session_cache.clear()
    try:
        with patch("memex.mcp_server.tools_write.resolve_project_id", return_value=None):
            mock_client = AsyncMock()
            mock_client.add_episode.return_value = MagicMock()

            name_1 = await _get_or_create_session(mock_client, "/tmp/repo", agent="claude-code")
            name_2 = await _get_or_create_session(mock_client, "/tmp/repo", agent="claude-code")

            assert name_1 == name_2
            assert mock_client.add_episode.call_count == 1
    finally:
        tw._session_cache.clear()


@pytest.mark.asyncio
async def test_record_decision_corroborates_threads_agent_to_session():
    """record_decision(corroborates=..., agent=...) must not raise TypeError,
    and the agent identity must reach `_get_or_create_session` via
    `_corroborate_decision` (Pitfall 2 — the easy-to-miss private helper)."""
    with (
        patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client,
        patch(
            "memex.mcp_server.tools_write._get_or_create_session",
            new_callable=AsyncMock,
            return_value="session_xyz",
        ) as mock_get_session,
    ):
        mock_client = AsyncMock()
        mock_records = MagicMock()
        mock_records.records = [{"name": "Switched to EdDSA"}]
        mock_client.driver.execute_query.return_value = mock_records
        mock_get_client.return_value = mock_client

        result = await record_decision(text="any", corroborates="abc123", agent="gemini-cli")

    assert "corroborated" in result
    mock_get_session.assert_called_once()
    call = mock_get_session.call_args
    call_agent = call.kwargs.get("agent")
    if call_agent is None and len(call.args) >= 3:
        call_agent = call.args[2]
    assert call_agent == "gemini-cli"


# ---------------------------------------------------------------------------
# Phase 01 Plan 01 Task 2 — n.harness node property (NET-05)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_decision_sets_harness_node_property():
    """record_decision(agent="claude-code")'s node-update SET clause must
    include n.harness and params must carry the real agent string (NET-05)."""
    mock_result = MagicMock()
    mock_result.episode.uuid = "uuid-harness-1"

    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.add_episode.return_value = mock_result
        mock_get_client.return_value = mock_client

        result = await record_decision(
            text="Use RS256 for JWT tokens.",
            agent="claude-code",
        )

    assert "decision recorded" in result

    found = False
    for call in mock_client.driver.execute_query.call_args_list:
        cypher = call.args[0] if call.args else call.kwargs.get("query", "")
        params = call.kwargs.get("params", {})
        if "n.harness" in cypher:
            found = True
            assert params.get("agent") == "claude-code"
    assert found, "expected a SET clause containing n.harness with agent in params"


@pytest.mark.asyncio
async def test_record_problem_sets_harness_node_property():
    """record_problem(agent="gemini-cli")'s node-update SET clause must
    include n.harness and params must carry the real agent string (NET-05)."""
    mock_result = MagicMock()
    mock_result.episode.uuid = "prob-harness-1"

    with patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.search.return_value = []
        mock_client.add_episode.return_value = mock_result
        mock_get_client.return_value = mock_client

        result = await record_problem(
            text="Memory leak in watcher daemon.",
            agent="gemini-cli",
        )

    assert "problem recorded" in result

    found = False
    for call in mock_client.driver.execute_query.call_args_list:
        cypher = call.args[0] if call.args else call.kwargs.get("query", "")
        params = call.kwargs.get("params", {})
        if "n.harness" in cypher:
            found = True
            assert params.get("agent") == "gemini-cli"
    assert found, "expected a SET clause containing n.harness with agent in params"
