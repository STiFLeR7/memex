import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, UTC
from memex.watcher.handlers import (
    handle_file_change, corroborate_decisions, initial_lockfile_index,
)
from memex.watcher.events import FileChangeEvent


@pytest.mark.asyncio
async def test_initial_lockfile_index_scans_once_with_canonical_repo():
    """Audit B3 — IMPORTS/Dependency edges must be built at startup, not only
    on a lockfile *change* event. And the stored repo_path must be canonical
    so predict_impact's import dimension can find them (B1)."""
    with patch("memex.watcher.handlers.extract_dependencies", new=AsyncMock(return_value=["dep"])), \
         patch("memex.watcher.handlers.extract_module_imports", new=AsyncMock(return_value=[("a", "b", {})])), \
         patch("memex.watcher.handlers.write_lockfile_delta", new=AsyncMock(return_value={"deps_written": 1, "edges_written": 1})) as mock_write, \
         patch("memex.watcher.handlers.canonical_repo_path", return_value="/canon/repo"):
        await initial_lockfile_index("D:/Canon/Repo")

    mock_write.assert_awaited_once()
    assert mock_write.call_args.args[0] == "/canon/repo"

@pytest.mark.asyncio
async def test_handler_error_logs_traceback_not_crashes():
    """
    Mock the extractor to raise, assert the daemon continues 
    and the error was logged with exc_info=True.
    """
    event = FileChangeEvent(
        path="fake_file.py",
        repo_root=".",
        kind="modified",
        timestamp=datetime.now(UTC)
    )
    
    # We need to mock Path and git commands to get past the initial setup
    with patch("memex.watcher.handlers.Path") as mock_path:
        # Mock repo root resolution (the while loop)
        mock_repo = MagicMock()
        mock_repo.__truediv__.return_value.exists.return_value = True
        mock_path.return_value.parent = mock_repo
        
        # Mock relpath
        with patch("os.path.relpath", return_value="fake_file.py"):
            # Mock extract_symbol_delta to raise
            with patch("memex.watcher.handlers.extract_symbol_delta", side_effect=Exception("Simulated error")):
                # Patch the logger in the handler module
                with patch("memex.watcher.handlers.logger") as mock_logger, \
                     patch("memex.watcher.handlers.health") as mock_health:
                    # This should not raise
                    await handle_file_change(event)

                    # Verify error was logged
                    assert mock_logger.error.called
                    args, kwargs = mock_logger.error.call_args
                    assert "unhandled error in handle_file_change" in args[0]
                    assert kwargs.get("exc_info") is True
                    # And the error was recorded to watcher health (Q1).
                    mock_health.record.assert_called_once()
                    assert mock_health.record.call_args.kwargs.get("errors") == 1


# ---------------------------------------------------------------------------
# v0.3.0 Phase 8 — corroboration is *evidence*, not validation.
# ---------------------------------------------------------------------------


def _make_mock_client_with_decisions(decisions):
    """Build a mocked graph client whose first execute_query call returns the
    given decision records and whose subsequent UPDATE calls are observable
    via ``update_calls``."""
    mock_client = AsyncMock()
    mock_driver = AsyncMock()
    mock_client.driver = mock_driver

    # Build a fake records object
    records = []
    for d in decisions:
        rec = MagicMock()
        # Records support both __getitem__ and .data()
        def _make_getitem(d):
            return lambda key: d.get(key)
        rec.__getitem__.side_effect = _make_getitem(d)
        rec.get.side_effect = lambda key, default=None, _d=d: _d.get(key, default)
        records.append(rec)

    select_res = MagicMock()
    select_res.records = records
    update_res = MagicMock()
    update_res.records = []

    update_calls = []

    async def fake_execute_query(query, params=None, **kw):
        if "MATCH (d:Entity)" in query and "RETURN d.uuid" in query:
            return select_res
        update_calls.append((query, params))
        return update_res

    mock_driver.execute_query.side_effect = fake_execute_query
    return mock_client, update_calls


@pytest.mark.asyncio
async def test_corroboration_lifts_last_reinforced_at():
    """When the corroboration matcher fires, the SET clause must include
    ``d.last_reinforced_at = $now``."""
    decisions = [
        {
            "id": "uuid-1",
            "eid": "elem-1",
            "text": "Switched authentication tokens to EdDSA signing",
            "related_entities": ["auth.py"],
        }
    ]
    mock_client, update_calls = _make_mock_client_with_decisions(decisions)

    with patch("memex.watcher.handlers.get_graph_client", return_value=mock_client), \
         patch("memex.graph.cluster_summary._embed_text", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = [1.0, 0.0]
        count = await corroborate_decisions(
            repo_root=".",
            sha="deadbeefcafebabe",
            # Use enough overlapping significant words to trigger a match.
            message="Updated EdDSA signing for authentication tokens",
            files_changed=["auth.py"],
        )

    assert count == 1
    assert len(update_calls) == 1
    update_query, params = update_calls[0]
    assert "SET d.last_reinforced_at = $now" in update_query
    assert params["sha"] == "deadbeefcafebabe"


@pytest.mark.asyncio
async def test_corroboration_does_not_set_validated_true():
    """Corroboration is evidence, not validation. The update Cypher must NOT
    set ``validated = true`` anywhere."""
    decisions = [
        {
            "id": "uuid-2",
            "eid": "elem-2",
            "text": "Switched authentication tokens to EdDSA signing",
            "related_entities": ["auth.py"],
        }
    ]
    mock_client, update_calls = _make_mock_client_with_decisions(decisions)

    with patch("memex.watcher.handlers.get_graph_client", return_value=mock_client), \
         patch("memex.graph.cluster_summary._embed_text", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = [1.0, 0.0]
        await corroborate_decisions(
            repo_root=".",
            sha="abcdef0123456789",
            message="Updated EdDSA signing for authentication tokens",
            files_changed=["auth.py"],
        )

    assert update_calls, "expected the corroboration update to have fired"
    update_query, _ = update_calls[0]
    assert "validated" not in update_query.lower(), (
        "corroboration must NOT set validated — only `memex review` can validate"
    )


@pytest.mark.asyncio
async def test_corroboration_does_not_overwrite_confidence_field():
    """v0.3.0 confidence is computed at query time, never stored-and-mutated.
    The update Cypher must NOT write ``d.confidence = ...``."""
    decisions = [
        {
            "id": "uuid-3",
            "eid": "elem-3",
            "text": "Switched authentication tokens to EdDSA signing",
            "related_entities": ["auth.py"],
        }
    ]
    mock_client, update_calls = _make_mock_client_with_decisions(decisions)

    with patch("memex.watcher.handlers.get_graph_client", return_value=mock_client), \
         patch("memex.graph.cluster_summary._embed_text", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = [1.0, 0.0]
        await corroborate_decisions(
            repo_root=".",
            sha="abcdef0123456789",
            message="Updated EdDSA signing for authentication tokens",
            files_changed=["auth.py"],
        )

    assert update_calls, "expected the corroboration update to have fired"
    update_query, _ = update_calls[0]
    assert "d.confidence" not in update_query, (
        "corroboration must NOT overwrite d.confidence — it's computed at query time in v0.3.0"
    )
