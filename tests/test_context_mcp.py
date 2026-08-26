from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from memex.context.packet import ContextItem, ContextPacket
from memex.context.selection import select_context
from memex.integrations.hermes_provider import HermesMemexProvider
from memex.mcp_server.context_tools import get_engineering_context


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def _packet():
    return ContextPacket.from_items(
        packet_id="packet-mcp-1",
        repository={"repo_path": "D:/repo"},
        task={"query": "understand retrieval"},
        items=[
            ContextItem(
                item_id="decision-1",
                entity_type="Decision",
                ref="decision-1",
                summary="Use the existing retrieval path.",
                scope="D:/repo",
                confidence=0.9,
                freshness="current",
                source_refs=[
                    {"kind": "commit", "ref": "abc123", "observed_at": NOW}
                ],
                selection_reason=["same repository", "query relevance"],
            )
        ],
    )


@pytest.mark.asyncio
async def test_mcp_projection_uses_shared_selector_and_packet_renderer():
    with patch(
        "memex.mcp_server.context_tools.select_context",
        new=AsyncMock(return_value=_packet()),
    ) as selector:
        result = await get_engineering_context(
            "understand retrieval", repo="D:/repo", top_k=50, task_id="task-1"
        )

    assert "<memex-context packet=packet-mcp-1" in result
    assert "Source: commit:abc123" in result
    kwargs = selector.call_args.kwargs
    assert kwargs["repo"] == "D:/repo"
    assert kwargs["task_id"] == "task-1"
    assert kwargs["budget"].max_items == 8


@pytest.mark.asyncio
async def test_mcp_projection_requires_query_and_repository_scope():
    assert "query must be non-empty" in await get_engineering_context(" ", repo="D:/repo")
    assert "repository or project scope is required" in await get_engineering_context("query")


@pytest.mark.asyncio
async def test_mcp_projection_fails_open_on_selector_error():
    with patch(
        "memex.mcp_server.context_tools.select_context",
        new=AsyncMock(side_effect=RuntimeError("backend down")),
    ):
        result = await get_engineering_context("query", repo="D:/repo")

    assert "engineering context temporarily unavailable" in result


@pytest.mark.asyncio
async def test_mcp_and_provider_project_equivalent_context_from_shared_selector():
    async def retrieve(query, num_results, repo, project):
        return [
            {
                "uuid": "decision-shared",
                "type": "Decision",
                "text": "Use the shared retrieval path.",
                "repo_path": repo,
                "source_commit": "abc123",
                "created_at": NOW,
                "confidence": 0.9,
            }
        ]

    async def shared_selector(query, **kwargs):
        return await select_context(
            query,
            repo=kwargs["repo"],
            project=kwargs.get("project"),
            task_id=kwargs.get("task_id"),
            session_id=kwargs.get("session_id"),
            agent_id=kwargs.get("agent_id"),
            harness=kwargs.get("harness"),
            budget=kwargs["budget"],
            allow_historical=kwargs.get("allow_historical", False),
            retriever=retrieve,
        )

    provider = HermesMemexProvider(
        {"repo_path": "D:/repo", "prefetch_timeout_seconds": 1},
        selector=shared_selector,
    )
    provider.initialize("session-shared")
    provider_projection = provider.prefetch("understand retrieval")

    with patch(
        "memex.mcp_server.context_tools.select_context",
        new=shared_selector,
    ):
        mcp_projection = await get_engineering_context(
            "understand retrieval", repo="D:/repo", session_id="session-shared"
        )

    for projection in (provider_projection, mcp_projection):
        assert "Use the shared retrieval path." in projection
        assert "ref=decision-shared" in projection
        assert "Source: commit:abc123" in projection
        assert "Why:" in projection


@pytest.mark.asyncio
async def test_mcp_drops_invalid_packet_metadata_fail_open():
    payload = _packet().model_dump(mode="python")
    payload["items"][0]["source_refs"] = [{"kind": "commit", "ref": "abc123"}]
    invalid_packet = ContextPacket.model_validate(payload)
    with patch(
        "memex.mcp_server.context_tools.select_context",
        new=AsyncMock(return_value=invalid_packet),
    ):
        result = await get_engineering_context("query", repo="D:/repo")

    assert "invalid context metadata" in result
