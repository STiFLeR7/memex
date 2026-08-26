from datetime import datetime, timedelta, timezone

import pytest

from memex.context import ContextPacket
from memex.context.packet import PacketBudget
from memex.context.selection import select_context
from memex.mcp_server.reranker import ScoredResult


NOW = datetime.now(timezone.utc)


def _result(ref: str, **overrides):
    value = {
        "uuid": ref,
        "type": "Decision",
        "text": f"Decision {ref}",
        "repo_path": "D:/repo",
        "source_commit": f"commit-{ref}",
        "created_at": NOW,
        "confidence": 0.9,
        "score": 0.8,
    }
    value.update(overrides)
    return value


def _scored(value: dict, *, final_score: float = 0.8) -> ScoredResult:
    return ScoredResult(
        uuid=value["uuid"],
        node_or_edge=value,
        modality="edge",
        graphiti_score=value.get("score", 0.8),
        recency_factor=0.95,
        confidence_factor=0.95,
        rehearsal_boost=1.0,
        final_score=final_score,
        rrf_score=0.016,
    )


@pytest.mark.asyncio
async def test_retrieval_results_become_explainable_context_packet():
    calls = {}

    async def retrieve(query, num_results, repo, project):
        calls.update(query=query, num_results=num_results, repo=repo, project=project)
        return [_scored(_result("decision-1"))]

    packet = await select_context(
        "cache invalidation",
        repo="D:/repo",
        task_id="task-1",
        session_id="session-1",
        retriever=retrieve,
    )

    assert isinstance(packet, ContextPacket)
    assert calls == {
        "query": "cache invalidation",
        "num_results": 8,
        "repo": "D:/repo",
        "project": None,
    }
    assert packet.task.task_id == "task-1"
    assert packet.execution.session_id == "session-1"
    assert packet.items[0].ref == "decision-1"
    assert "same repository" in packet.items[0].selection_reason
    assert "query relevance" in packet.items[0].selection_reason
    assert packet.items[0].source_refs[0].kind == "commit"
    assert packet.items[0].score_breakdown["final_score"] == 0.8


@pytest.mark.asyncio
async def test_existing_provenance_gets_observation_time_from_retrieved_entity():
    async def retrieve(query, num_results, repo, project):
        return [_result("decision-1", source_refs=[{"kind": "human", "ref": "issue-1"}])]

    packet = await select_context("observation time", repo="D:/repo", retriever=retrieve)

    assert packet.items[0].source_refs[0].ref == "issue-1"
    assert packet.items[0].source_refs[0].observed_at == NOW


@pytest.mark.asyncio
async def test_graphiti_edge_attributes_supply_scope_and_provenance():
    class GraphitiEdge:
        uuid = "edge-1"
        fact = "Graphiti stores custom edge metadata in attributes."
        created_at = NOW
        attributes = {
            "repo_path": "D:/repo",
            "source": "goal10-evaluation-seed",
            "source_kind": "derived",
        }

    async def retrieve(query, num_results, repo, project):
        return [GraphitiEdge()]

    packet = await select_context("edge metadata", repo="D:/repo", retriever=retrieve)

    assert packet.items[0].ref == "edge-1"
    assert packet.items[0].source_refs[0].ref == "goal10-evaluation-seed"


@pytest.mark.asyncio
async def test_missing_source_provenance_is_excluded_from_context():
    async def retrieve(query, num_results, repo, project):
        return [_result("missing-source", source_commit=None, source_refs=[])]

    packet = await select_context("missing provenance", repo="D:/repo", retriever=retrieve)

    assert packet.items == []
    assert "missing-source" in packet.projection.dropped_items


@pytest.mark.asyncio
async def test_empty_retrieval_returns_empty_packet():
    async def retrieve(query, num_results, repo, project):
        return []

    packet = await select_context("unknown subsystem", repo="D:/repo", retriever=retrieve)

    assert packet.items == []
    assert packet.selection.candidate_count == 0
    assert packet.budget.actual_chars > 0


@pytest.mark.asyncio
async def test_stale_result_is_labeled_not_silently_presented_as_current():
    async def retrieve(query, num_results, repo, project):
        return [_result("stale-1", stale=True)]

    packet = await select_context("old decision", repo="D:/repo", retriever=retrieve)

    assert packet.items[0].freshness == "stale"
    assert "stale or aging" in packet.items[0].selection_reason


@pytest.mark.asyncio
async def test_superseded_result_is_excluded_unless_history_is_requested():
    async def retrieve(query, num_results, repo, project):
        return [_result("old-1", superseded_by=["new-1"])]

    current = await select_context("current decision", repo="D:/repo", retriever=retrieve)
    historical = await select_context(
        "historical decision",
        repo="D:/repo",
        retriever=retrieve,
        allow_historical=True,
    )

    assert current.items == []
    assert "old-1" in current.projection.dropped_items
    assert historical.items[0].freshness == "superseded"
    assert historical.items[0].superseded_by == ["new-1"]


@pytest.mark.asyncio
async def test_conflicting_result_is_returned_with_warning_reason():
    async def retrieve(query, num_results, repo, project):
        return [_result("conflict-1", conflicted=True)]

    packet = await select_context("conflicting decision", repo="D:/repo", retriever=retrieve)

    assert packet.items[0].freshness == "conflicted"
    assert "conflicting evidence" in packet.items[0].selection_reason


@pytest.mark.asyncio
async def test_duplicate_results_are_deduplicated_by_stable_reference():
    async def retrieve(query, num_results, repo, project):
        return [_result("same-1"), _result("same-1", text="duplicate")]

    packet = await select_context("duplicate decision", repo="D:/repo", retriever=retrieve)

    assert [item.ref for item in packet.items] == ["same-1"]
    assert packet.selection.candidate_count == 2
    assert "same-1" in packet.projection.dropped_items


@pytest.mark.asyncio
async def test_budget_truncation_keeps_retrieval_order_and_records_drop():
    async def retrieve(query, num_results, repo, project):
        return [_scored(_result("first"), final_score=0.9), _scored(_result("second"), final_score=0.8)]

    packet = await select_context(
        "bounded context",
        repo="D:/repo",
        retriever=retrieve,
        budget=PacketBudget(max_items=1, max_chars=12_000),
    )

    assert [item.ref for item in packet.items] == ["first"]
    assert "second" in packet.projection.dropped_items


@pytest.mark.asyncio
async def test_expired_result_is_excluded_by_existing_validity_policy():
    async def retrieve(query, num_results, repo, project):
        return [_result("expired-1", expired_at=NOW - timedelta(days=1))]

    packet = await select_context("expired decision", repo="D:/repo", retriever=retrieve)

    assert packet.items == []
    assert "expired-1" in packet.projection.dropped_items
