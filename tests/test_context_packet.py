from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from memex.context.packet import ContextPacket


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def _item(*, item_id: str = "decision-1", ref: str = "graph-1", summary: str = "Use Neo4j") -> dict:
    return {
        "item_id": item_id,
        "entity_type": "Decision",
        "ref": ref,
        "summary": summary,
        "scope": "memex/memex/graph",
        "confidence": 0.9,
        "freshness": "current",
        "source_refs": [{"kind": "commit", "ref": "abc123", "observed_at": NOW}],
        "selection_reason": ["same repository", "high confidence"],
    }


def _packet(**kwargs) -> ContextPacket:
    values = {
        "packet_id": "packet-1",
        "repository": {"repo_path": "D:/memex"},
        "task": {"query": "Understand graph retrieval"},
        "created_at": NOW,
        "budget": {"max_items": 8, "max_chars": 4000},
        "items": [_item()],
    }
    values.update(kwargs)
    return ContextPacket(**values)


def test_valid_packet_serializes_deterministically():
    packet = _packet()

    first = packet.to_deterministic_json()
    second = ContextPacket.model_validate_json(first).to_deterministic_json()

    assert first == second
    assert '"packet_id":"packet-1"' in first


def test_packet_requires_repository_scope():
    with pytest.raises(ValidationError):
        _packet(repository={})


def test_packet_requires_item_provenance():
    item = _item()
    item["source_refs"] = []

    with pytest.raises(ValidationError):
        _packet(items=[item])


def test_stale_knowledge_is_retained_with_explicit_freshness():
    item = _item()
    item["freshness"] = "stale"

    packet = _packet(items=[item])

    assert packet.items[0].freshness == "stale"
    assert "stale" in packet.render_text()


def test_duplicate_knowledge_is_deduplicated_by_reference():
    duplicate = _item(item_id="decision-duplicate")

    packet = ContextPacket.from_items(
        packet_id="packet-1",
        repository={"repo_path": "D:/memex"},
        task={"query": "Understand graph retrieval"},
        created_at=NOW,
        budget={"max_items": 8, "max_chars": 4000},
        items=[_item(), duplicate],
    )

    assert [item.item_id for item in packet.items] == ["decision-1"]
    assert packet.projection.dropped_items == ["decision-duplicate"]


def test_budget_truncates_items_and_records_dropped_items():
    packet = ContextPacket.from_items(
        packet_id="packet-1",
        repository={"repo_path": "D:/memex"},
        task={"query": "Understand graph retrieval"},
        created_at=NOW,
        budget={"max_items": 1, "max_chars": 4000},
        items=[_item(), _item(item_id="problem-1", ref="graph-2", summary="Watcher is slow")],
    )

    assert len(packet.items) == 1
    assert packet.projection.dropped_items == ["problem-1"]
    assert len(packet.render_text()) <= packet.budget.max_chars


def test_oversized_packet_is_rejected():
    with pytest.raises(ValidationError, match="budget"):
        _packet(
            budget={"max_items": 8, "max_chars": 32},
            items=[_item(summary="x" * 500)],
        )


def test_duplicate_packet_items_are_rejected_when_constructed_directly():
    with pytest.raises(ValidationError, match="duplicate"):
        _packet(items=[_item(), _item(item_id="decision-duplicate")])


def test_packet_serializes_dates_and_budget_metadata():
    packet = _packet()

    data = packet.model_dump(mode="json")

    assert data["created_at"] == "2026-08-24T12:00:00Z"
    assert data["budget"]["max_chars"] == 4000
    assert data["items"][0]["source_refs"][0]["kind"] == "commit"
