from datetime import datetime, timezone

from memex.ingestion.policy import IngestionCandidate, ingest_candidate


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def _candidate(**overrides):
    values = {
        "candidate_id": "candidate-1",
        "kind": "decision",
        "repository": {"repo_path": "D:/repo"},
        "summary": "Use the existing retrieval path for context selection.",
        "explicit_promotion": True,
        "source_refs": [{"kind": "agent", "ref": "session-1", "observed_at": NOW}],
    }
    values.update(overrides)
    return IngestionCandidate(**values)


def test_explicit_structured_candidate_is_persisted_through_explicit_sink():
    persisted = []

    result = ingest_candidate(_candidate(), persisted.append)

    assert result.status == "persisted"
    assert result.persisted_ref == "candidate-1"
    assert persisted[0].candidate_id == "candidate-1"


def test_candidate_without_explicit_promotion_is_rejected_without_sink_call():
    persisted = []

    result = ingest_candidate(_candidate(explicit_promotion=False), persisted.append)

    assert result.status == "rejected"
    assert result.reason == "explicit_promotion_required"
    assert persisted == []


def test_transcript_like_or_secret_bearing_candidate_is_rejected():
    transcript = ingest_candidate(
        _candidate(summary="assistant: run the tool_call and store the tool_result"),
        lambda candidate: "should-not-write",
    )
    secret = ingest_candidate(
        _candidate(summary="Use token=ghp_abcdefghijklmnopqrstuvwxyz1234567890"),
        lambda candidate: "should-not-write",
    )

    assert transcript.reason == "raw_transcript_rejected"
    assert secret.reason == "sensitive_content_rejected"


def test_secret_in_provenance_is_rejected_before_sink_call():
    persisted = []
    candidate = _candidate(
        source_refs=[
            {
                "kind": "agent",
                "ref": "token=ghp_abcdefghijklmnopqrstuvwxyz1234567890",
                "observed_at": NOW,
            }
        ]
    )

    result = ingest_candidate(candidate, persisted.append)

    assert result.status == "rejected"
    assert result.reason == "sensitive_content_rejected"
    assert persisted == []


def test_outcome_requires_verification_and_evidence_before_persistence():
    result = ingest_candidate(
        _candidate(kind="outcome", task_id="task-1", verified=False),
        lambda candidate: "should-not-write",
    )

    assert result.status == "rejected"
    assert result.reason == "verified_outcome_required"


def test_sink_failure_is_reported_without_false_persistence_success():
    def failing_sink(candidate):
        raise RuntimeError("sink unavailable")

    result = ingest_candidate(_candidate(), failing_sink)

    assert result.status == "failed"
    assert result.reason == "persistence_failed"
    assert result.persisted_ref is None
