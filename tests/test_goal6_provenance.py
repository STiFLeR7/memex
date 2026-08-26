from datetime import datetime, timezone

from memex.context.packet import ContextPacket, validate_packet_metadata
from memex.integrations.hermes_provider import HermesMemexProvider


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def _packet(*, observed_at=True, freshness="current", superseded_by=None):
    source = {"kind": "commit", "ref": "abc123"}
    if observed_at:
        source["observed_at"] = NOW
    return ContextPacket.from_items(
        packet_id="packet-goal6",
        repository={"repo_path": "D:/repo"},
        task={"query": "verify context metadata"},
        created_at=NOW,
        items=[
            {
                "item_id": "decision-1",
                "entity_type": "Decision",
                "ref": "decision-1",
                "summary": "Use the verified retrieval path.",
                "scope": "D:/repo",
                "confidence": 0.9,
                "freshness": freshness,
                "superseded_by": superseded_by or [],
                "source_refs": [source],
                "selection_reason": ["same repository", "fresh/current"],
            }
        ],
    )


def test_packet_metadata_verification_requires_observed_provenance():
    packet = _packet(observed_at=False)

    assert "missing_observed_at:decision-1" in validate_packet_metadata(packet)


def test_packet_metadata_verification_accepts_current_provenance():
    assert validate_packet_metadata(_packet()) == []


def test_packet_metadata_verification_requires_supersession_link():
    packet = _packet(freshness="superseded")

    assert "missing_superseded_by:decision-1" in validate_packet_metadata(packet)


def test_packet_metadata_verification_rejects_reversed_validity_window():
    packet = _packet()
    packet = packet.model_copy(
        update={
            "items": [
                packet.items[0].model_copy(
                    update={"valid_from": NOW, "valid_until": NOW}
                )
            ]
        }
    )

    assert "invalid_validity_window:decision-1" in validate_packet_metadata(packet)


def test_provider_fails_open_when_packet_metadata_is_not_explainable():
    async def select(query, **kwargs):
        return _packet(observed_at=False)

    provider = HermesMemexProvider(
        config={"repo_path": "D:/repo", "prefetch_timeout_seconds": 1},
        selector=select,
    )
    provider.initialize(session_id="session-1")

    assert provider.prefetch("verify metadata") == ""
