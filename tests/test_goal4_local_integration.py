from pathlib import Path

from memex.evaluation.local_vertical_slice import run_local_vertical_slice


def test_goal4_local_slice_reports_bounded_useful_treatment_context():
    report = run_local_vertical_slice(Path(__file__).parents[1])

    assert report["task_id"] == "goal4-retrieval-pipeline"
    assert report["improvement_claim"] == "not_established"
    assert report["baseline"]["task_outcome"] == "PARTIAL"
    assert report["treatment"]["task_outcome"] == "SUCCESS"

    baseline = report["baseline"]
    treatment = report["treatment"]
    assert baseline["context_returned"] is False
    assert treatment["context_returned"] is True
    assert treatment["context_chars"] <= 12_000
    assert treatment["packet_id"]
    assert treatment["retrieval_id"] == "goal4-local-fixture"
    assert treatment["selected_entities"]
    assert treatment["selection_reasons"]
    assert treatment["useful_context"] is True
    assert treatment["verification"] == ["repository_state_verified", "context_contract_verified"]
    assert treatment["failures"] == []
