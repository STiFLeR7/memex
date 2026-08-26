from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from memex.context.task_outcome import OutcomeRecord, TaskRecord
from memex.evaluation.harness import evaluate_local_vertical_slice


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def _task(**overrides):
    values = {
        "task_id": "task-1",
        "repository": {"repo_path": "D:/repo"},
        "normalized_intent": "Understand the retrieval pipeline.",
        "scope": "retrieval",
        "acceptance_criteria": ["Identify the retrieval entrypoint."],
        "status": "in_progress",
        "created_at": NOW,
        "source_refs": [{"kind": "derived", "ref": "fixture:task", "observed_at": NOW}],
    }
    values.update(overrides)
    return TaskRecord(**values)


def test_task_record_is_bounded_and_repository_scoped():
    task = _task()

    assert task.task_id == "task-1"
    assert task.repository.repo_path == "D:/repo"
    assert task.acceptance_criteria == ["Identify the retrieval entrypoint."]
    assert "normalized_intent" in task.to_deterministic_json()


def test_task_requires_acceptance_criteria_or_verification_target():
    with pytest.raises(ValidationError, match="acceptance criteria"):
        _task(acceptance_criteria=[], verification_target=None)


def test_successful_outcome_requires_verification_evidence():
    with pytest.raises(ValidationError, match="verification"):
        OutcomeRecord(
            outcome_id="outcome-1",
            task_id="task-1",
            session_id="session-1",
            status="success",
            recorded_at=NOW,
            summary="Completed.",
        )


def test_outcome_links_task_session_packet_and_verification():
    outcome = OutcomeRecord(
        outcome_id="outcome-1",
        task_id="task-1",
        session_id="session-1",
        packet_id="packet-1",
        status="success",
        verification_refs=[
            {"kind": "derived", "ref": "verification:tests", "observed_at": NOW}
        ],
        checks=["tests"],
        recorded_at=NOW,
        summary="Completed and verified.",
    )

    assert outcome.packet_id == "packet-1"
    assert outcome.verification_refs[0].ref == "verification:tests"


def test_local_evaluation_exposes_task_and_outcome_records():
    evaluation = evaluate_local_vertical_slice(r"D:\memex")

    assert evaluation["task"]["task_id"] == "goal4-retrieval-pipeline"
    assert evaluation["outcomes"]["baseline"]["status"] == "partial"
    assert evaluation["outcomes"]["treatment"]["status"] == "success"
    assert evaluation["outcomes"]["treatment"]["packet_id"] == "packet-goal4-local"


def test_local_evaluation_preserves_task_execution_packet_outcome_join():
    evaluation = evaluate_local_vertical_slice(r"D:\memex")
    task = evaluation["task"]
    treatment = evaluation["outcomes"]["treatment"]

    assert treatment["task_id"] == task["task_id"]
    assert treatment["session_id"] == evaluation["treatment"]["session_id"]
    assert treatment["packet_id"] == evaluation["treatment"]["packet_id"]
    assert treatment["outcome_id"] == evaluation["treatment"]["outcome_id"]
