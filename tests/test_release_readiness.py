from memex.evaluation.release_readiness import (
    EVALUATION_CASES,
    run_evaluation_matrix,
)


def _run(case, arm, status="SUCCESS"):
    return {
        "arm": arm,
        "task_id": case.case_id,
        "session_id": f"{case.case_id}-{arm}",
        "task_outcome": status,
        "context_returned": arm == "treatment",
        "packet_id": f"packet-{case.case_id}" if arm == "treatment" else None,
        "retrieval_id": f"retrieval-{case.case_id}" if arm == "treatment" else None,
        "verification": ["repository_state_verified"],
        "failures": [],
    }


def test_matrix_contains_the_approved_eight_task_categories():
    assert len(EVALUATION_CASES) == 8
    assert EVALUATION_CASES[0].slug == "unfamiliar-repository"
    assert EVALUATION_CASES[-1].slug == "parallel-conflicts"


def test_matrix_without_live_runner_is_explicitly_not_release_ready():
    report = run_evaluation_matrix()

    assert report["case_count"] == 8
    assert report["invalid_count"] == 8
    assert report["release_decision"] == "NO-GO"
    assert report["evidence_status"] == "not_executed"


def test_matrix_can_evaluate_all_cases_with_an_injected_runner():
    report = run_evaluation_matrix(
        lambda case: {
            "baseline": _run(case, "baseline"),
            "treatment": _run(case, "treatment"),
        }
    )

    assert report["valid_count"] == 8
    assert report["invalid_count"] == 0
    assert report["release_decision"] == "GO"
    assert report["evidence_status"] == "paired_runs"


def test_matrix_rejects_a_treatment_failure():
    def runner(case):
        return {
            "baseline": _run(case, "baseline"),
            "treatment": _run(case, "treatment", status="FAILURE"),
        }

    report = run_evaluation_matrix(runner)

    assert report["release_decision"] == "NO-GO"
    assert report["failure_count"] == 8
