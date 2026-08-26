import asyncio
from datetime import datetime, timezone

from memex.context.packet import ContextItem, ContextPacket
from memex.config import canonical_repo_path
from memex.integrations.hermes_provider import HermesMemexProvider


NOW = datetime.now(timezone.utc)


def _packet(**kwargs):
    return ContextPacket.from_items(
        repository={"repo_path": "D:/repo"},
        task={"query": kwargs.pop("query", "understand retrieval")},
        items=[
            ContextItem(
                item_id="decision-1",
                entity_type="Decision",
                ref="decision-1",
                summary="Use the existing composite retrieval path.",
                scope="D:/repo",
                confidence=0.9,
                freshness="current",
                source_refs=[{"kind": "commit", "ref": "abc123", "observed_at": NOW}],
                selection_reason=["same repository", "query relevance"],
            )
        ],
        **kwargs,
    )


def test_provider_initializes_and_exposes_context_only_contract():
    provider = HermesMemexProvider(config={"repo_path": "D:/repo"})

    assert provider.name == "memex"
    assert provider.is_available() is True
    provider.initialize(session_id="session-1", agent_identity="coder")
    assert provider.get_tool_schemas() == []
    assert provider._session_id == "session-1"


def test_successful_prefetch_returns_bounded_provenance_aware_projection():
    calls = {}

    async def select(query, **kwargs):
        calls.update(query=query, **kwargs)
        return _packet(query=query)

    provider = HermesMemexProvider(
        config={"repo_path": "D:/repo", "prefetch_timeout_seconds": 1},
        selector=select,
    )
    provider.initialize(session_id="session-1")

    result = provider.prefetch("understand retrieval", session_id="session-2")

    assert "<memex-context" in result
    assert "Source: commit:abc123" in result
    assert calls["query"] == "understand retrieval"
    assert calls["repo"] == canonical_repo_path("D:/repo")
    assert calls["session_id"] == "session-2"
    assert calls["retrieval_id"].startswith("hermes-prefetch-")
    assert len(result) <= 12_000


def test_empty_or_uninitialized_prefetch_fails_open():
    async def select(query, **kwargs):
        raise AssertionError("selector should not run")

    provider = HermesMemexProvider(selector=select)

    assert provider.prefetch("task") == ""
    provider.initialize(session_id="session-1", repo_path="D:/repo")
    assert provider.prefetch("   ") == ""


def test_selector_failure_and_malformed_packet_fail_open():
    async def failing_select(query, **kwargs):
        raise RuntimeError("neo4j unavailable")

    provider = HermesMemexProvider(
        config={"repo_path": "D:/repo"},
        selector=failing_select,
    )
    provider.initialize(session_id="session-1")
    assert provider.prefetch("task") == ""

    async def malformed_select(query, **kwargs):
        return {"not": "a packet"}

    provider = HermesMemexProvider(
        config={"repo_path": "D:/repo"},
        selector=malformed_select,
    )
    provider.initialize(session_id="session-1")
    assert provider.prefetch("task") == ""


def test_prefetch_timeout_fails_open():
    async def slow_select(query, **kwargs):
        await asyncio.sleep(0.05)
        return _packet(query=query)

    provider = HermesMemexProvider(
        config={"repo_path": "D:/repo", "prefetch_timeout_seconds": 0.01},
        selector=slow_select,
    )
    provider.initialize(session_id="session-1")

    assert provider.prefetch("slow task") == ""


def test_provider_does_not_write_turns_or_expose_tools():
    provider = HermesMemexProvider(config={"repo_path": "D:/repo"})
    provider.initialize(session_id="session-1")

    assert provider.sync_turn("user", "assistant", session_id="session-1") is None
    assert provider.get_tool_schemas() == []
