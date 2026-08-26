from memex.evaluation.harness import (
    INVALID_RUN,
    SUCCESS,
    evaluate_paired_runs,
    evaluate_local_vertical_slice,
)


def test_harness_maps_goal4_slice_into_a_paired_evaluation():
    evaluation = evaluate_local_vertical_slice(r"D:\memex")

    assert evaluation["valid"] is True
    assert evaluation["task_id"] == "goal4-retrieval-pipeline"
    assert evaluation["baseline"]["status"] == "PARTIAL"
    assert evaluation["treatment"]["status"] == SUCCESS
    assert evaluation["task_success_delta"] == 1
    assert evaluation["evidence_status"] == "descriptive_only"
    assert evaluation["treatment"]["prefetch_latency_ms"] >= 0


def test_harness_marks_missing_trace_join_as_invalid_run():
    evaluation = evaluate_paired_runs(
        {
            "arm": "baseline",
            "task_id": "task-1",
            "session_id": "session-1",
            "task_outcome": SUCCESS,
        },
        {
            "arm": "treatment",
            "task_id": "task-1",
            "task_outcome": SUCCESS,
        },
    )

    assert evaluation["valid"] is False
    assert evaluation["treatment"]["status"] == INVALID_RUN
    assert "missing:session_id" in evaluation["treatment"]["failures"]


def test_harness_rejects_unverified_success_as_invalid_run():
    evaluation = evaluate_paired_runs(
        {
            "arm": "baseline",
            "task_id": "task-2",
            "session_id": "session-baseline",
            "task_outcome": SUCCESS,
            "verification": ["repository_state_verified"],
        },
        {
            "arm": "treatment",
            "task_id": "task-2",
            "session_id": "session-treatment",
            "task_outcome": SUCCESS,
            "verification": [],
        },
    )

    assert evaluation["valid"] is False
    assert evaluation["treatment"]["status"] == INVALID_RUN
    assert "missing:verification" in evaluation["treatment"]["failures"]
