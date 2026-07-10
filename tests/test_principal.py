"""Tests for memex.graph.principal (Phase 02 Plan 01, Task 2).

Persistence is exercised against a stub graph client so this suite stays
Neo4j-free, mirroring the stub pattern in tests/test_cluster_runner.py.
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import patch

import pytest

from memex.graph.principal import PRINCIPAL_MERGE_QUERY, write_principal_node


class _StubGraphClient:
    """Records every driver.execute_query call."""

    def __init__(self) -> None:
        self.queries: list[tuple[str, dict]] = []
        self.driver = self

    async def execute_query(self, query: str, params: dict | None = None) -> Any:
        self.queries.append((query, params or {}))
        return None


# ---------------------------------------------------------------------------
# write_principal_node — direct Cypher, never client.add_episode()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_principal_node_uses_merge_query_direct_cypher():
    client = _StubGraphClient()
    await write_principal_node(client, principal_id="alice", display_name="Alice", role="admin")

    assert len(client.queries) == 1
    query, params = client.queries[0]
    assert query == PRINCIPAL_MERGE_QUERY
    assert "MERGE (p:Entity {principal_id: $principal_id})" in query
    assert "p.type = 'Principal'" in query
    assert params["principal_id"] == "alice"
    assert params["display_name"] == "Alice"
    assert params["role"] == "admin"
    assert params["active"] is True
    assert "now" in params


@pytest.mark.asyncio
async def test_write_principal_node_defaults_active_true():
    client = _StubGraphClient()
    await write_principal_node(client, principal_id="bob", display_name=None, role="viewer")
    _, params = client.queries[0]
    assert params["active"] is True
    assert params["display_name"] is None


@pytest.mark.asyncio
async def test_write_principal_node_is_idempotent_merge_not_create():
    """Re-running for the same principal_id issues the MERGE query again
    (idempotent update), never a duplicate-creating CREATE."""
    client = _StubGraphClient()
    await write_principal_node(client, principal_id="carol", display_name="Carol", role="contributor")
    await write_principal_node(client, principal_id="carol", display_name="Carol", role="admin")

    assert len(client.queries) == 2
    for query, _ in client.queries:
        assert query.strip().startswith("MERGE")
        assert "CREATE (" not in query

    _, second_params = client.queries[1]
    assert second_params["role"] == "admin"


@pytest.mark.asyncio
async def test_write_principal_node_never_calls_add_episode():
    """Principal metadata is structured identity, not episodic narrative —
    must go through direct Cypher, never client.add_episode()."""

    class _ClientWithAddEpisode(_StubGraphClient):
        def __init__(self) -> None:
            super().__init__()
            self.add_episode_called = False

        async def add_episode(self, **kwargs):
            self.add_episode_called = True

    client = _ClientWithAddEpisode()
    await write_principal_node(client, principal_id="dave", display_name="Dave", role="admin")
    assert client.add_episode_called is False


# ---------------------------------------------------------------------------
# CLI wiring — `memex keys add <name> --role ...`
# ---------------------------------------------------------------------------


def test_cli_keys_add_with_role_calls_add_key_with_role_and_principal_id():
    from memex import cli

    with patch("memex.cli.add_key", return_value="mx_test") as mock_add:
        with patch("memex.cli.asyncio.run") as mock_asyncio_run:
            with patch("builtins.print"):
                with patch.object(sys, "argv", ["memex", "keys", "add", "alice", "--role", "viewer"]):
                    cli.main()
                    mock_add.assert_called_with("alice", role="viewer", principal_id="alice")
                    assert mock_asyncio_run.called


def test_cli_keys_add_default_role_is_admin():
    from memex import cli

    with patch("memex.cli.add_key", return_value="mx_test") as mock_add:
        with patch("memex.cli.asyncio.run"):
            with patch("builtins.print"):
                with patch.object(sys, "argv", ["memex", "keys", "add", "ci"]):
                    cli.main()
                    mock_add.assert_called_with("ci", role="admin", principal_id="ci")


def test_cli_keys_add_explicit_principal_id():
    from memex import cli

    with patch("memex.cli.add_key", return_value="mx_test") as mock_add:
        with patch("memex.cli.asyncio.run"):
            with patch("builtins.print"):
                with patch.object(
                    sys, "argv",
                    ["memex", "keys", "add", "ci-bot", "--role", "contributor", "--principal-id", "team-ci"],
                ):
                    cli.main()
                    mock_add.assert_called_with("ci-bot", role="contributor", principal_id="team-ci")


def test_cli_keys_add_succeeds_when_principal_bootstrap_raises():
    """Key creation must succeed and print the key even when the Neo4j
    Principal bootstrap write fails (Neo4j unreachable) — the registry-file
    key is the actual auth mechanism; the Principal node is supplementary
    team-visibility metadata for later phases (04/05)."""
    from memex import cli

    with patch("memex.cli.add_key", return_value="mx_survives_failure") as mock_add:
        with patch("memex.cli.asyncio.run", side_effect=RuntimeError("neo4j unreachable")):
            with patch("builtins.print") as mock_print:
                with patch.object(sys, "argv", ["memex", "keys", "add", "resilient"]):
                    cli.main()  # must not raise
                    mock_add.assert_called_once()
                    printed = "".join(str(call.args[0]) for call in mock_print.call_args_list)
                    assert "mx_survives_failure" in printed


def test_cli_keys_list_shows_principal_id_and_role_columns():
    from memex import cli

    mock_keys = [
        {"name": "ci", "principal_id": "ci", "role": "admin", "key_prefix": "mx_ab12cd34", "created_at": "now"},
    ]
    with patch("memex.cli.list_keys", return_value=mock_keys):
        with patch("builtins.print") as mock_print:
            with patch.object(sys, "argv", ["memex", "keys", "list"]):
                cli.main()
                printed = "".join(str(call.args[0]) for call in mock_print.call_args_list)
                assert "ci" in printed
                assert "admin" in printed
                assert "mx_ab12cd34" in printed
