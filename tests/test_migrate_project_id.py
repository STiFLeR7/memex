import sys
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from memex.graph.migrate_project_id import run_migrate_project_id_command


@pytest.mark.asyncio
async def test_migrate_project_id_issues_idempotent_backfill_query(tmp_path):
    repo_root = str(tmp_path)

    mock_res = MagicMock()
    mock_res.records = [{"updated": 7}]

    mock_client = AsyncMock()
    mock_client.driver.execute_query.return_value = mock_res

    with (
        patch(
            "memex.graph.migrate_project_id.get_graph_client",
            new_callable=AsyncMock,
            return_value=mock_client,
        ),
        patch(
            "memex.graph.migrate_project_id.resolve_project_id",
            return_value="github.com/acme/widgets",
        ),
        patch("builtins.print") as mock_print,
    ):
        await run_migrate_project_id_command(repo_root)

    assert mock_client.driver.execute_query.call_count == 1
    call = mock_client.driver.execute_query.call_args
    cypher = call.args[0] if call.args else call.kwargs.get("query", "")
    params = call.kwargs.get("params", call.args[1] if len(call.args) > 1 else {})

    assert "WHERE n.repo_path = $repo AND n.project_id IS NULL" in cypher
    assert "SET n.project_id = $project" in cypher
    assert params["project"] == "github.com/acme/widgets"
    # canonical path — just confirm it's the (abs) repo_root, canonicalized
    assert params["repo"]

    printed = "".join(str(c.args[0]) for c in mock_print.call_args_list)
    assert "github.com/acme/widgets" in printed
    assert "7" in printed


@pytest.mark.asyncio
async def test_migrate_project_id_errors_when_unresolvable(tmp_path):
    repo_root = str(tmp_path)

    mock_client = AsyncMock()

    with (
        patch(
            "memex.graph.migrate_project_id.get_graph_client",
            new_callable=AsyncMock,
            return_value=mock_client,
        ),
        patch("memex.graph.migrate_project_id.resolve_project_id", return_value=None),
        patch("builtins.print"),
    ):
        with pytest.raises(SystemExit) as exc:
            await run_migrate_project_id_command(repo_root)

    assert exc.value.code != 0
    assert not mock_client.driver.execute_query.called


def test_cli_migrate_project_id_dispatches():
    with (
        patch(
            "memex.graph.migrate_project_id.run_migrate_project_id_command",
            new_callable=AsyncMock,
        ) as mock_run,
        patch("memex.cli.asyncio.run") as mock_asyncio_run,
        patch.object(
            sys, "argv", ["memex", "migrate", "project-id", "--repo", "/fake/repo"]
        ),
    ):
        mock_asyncio_run.side_effect = lambda coro: None
        from memex import cli

        cli.main()
        assert mock_asyncio_run.called


@pytest.mark.integration
@pytest.mark.asyncio
async def test_migrate_project_id_idempotent_against_live_neo4j(tmp_path):
    """End-to-end: running the migration twice against a real Neo4j instance
    seeded with a repo_path-only node results in exactly one SET (first
    run) and zero further changes (second run). Skipped when no live Neo4j
    is reachable, matching the existing convention for integration-marked
    tests elsewhere in the suite."""
    try:
        from memex.graph.client import get_graph_client
        client = await get_graph_client()
        await client.driver.execute_query("RETURN 1")
    except Exception:
        pytest.skip("No live Neo4j instance reachable")

    repo_root = str(tmp_path)
    from memex.config import canonical_repo_path
    repo_path = canonical_repo_path(repo_root)

    # Seed a repo_path-only node.
    await client.driver.execute_query(
        "CREATE (n:Entity {uuid: $uuid, name: 'seed', repo_path: $repo})",
        params={"uuid": "seed-migrate-test", "repo": repo_path},
    )

    try:
        with patch(
            "memex.graph.migrate_project_id.resolve_project_id",
            return_value="github.com/acme/widgets",
        ):
            await run_migrate_project_id_command(repo_root)

            res = await client.driver.execute_query(
                "MATCH (n:Entity {uuid: $uuid}) RETURN n.project_id as project_id",
                params={"uuid": "seed-migrate-test"},
            )
            assert res.records[0]["project_id"] == "github.com/acme/widgets"

            # Second run — idempotent, no further changes.
            await run_migrate_project_id_command(repo_root)
            res2 = await client.driver.execute_query(
                "MATCH (n:Entity) WHERE n.repo_path = $repo AND n.project_id IS NULL "
                "RETURN count(n) as remaining",
                params={"repo": repo_path},
            )
            assert res2.records[0]["remaining"] == 0
    finally:
        await client.driver.execute_query(
            "MATCH (n:Entity {uuid: $uuid}) DETACH DELETE n",
            params={"uuid": "seed-migrate-test"},
        )
