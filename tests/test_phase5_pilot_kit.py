"""The maintainer pilot kit accepts outcome records only and summarizes them faithfully."""
import json

from memex.evaluation import pilot_kit


def test_template_records_validate_and_summary_counts_every_participant(tmp_path):
    log = tmp_path / "p1.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in pilot_kit.TEMPLATE))
    records, problems = pilot_kit.load([log])
    assert problems == [] and len(records) == 3
    summary = pilot_kit.summarize(records)
    assert summary["participants"] == 1 and summary["evidence_status"] == "pending"
    assert summary["by_mode"]["live"]["useful_corrections"] == 1 and summary["by_mode"]["shadow"]["tasks"] == 0
    assert summary["by_mode"]["live"]["cost_known"] == "0/1", "unknown cost is unknown, not zero"


def test_captured_content_and_impossible_counts_are_rejected():
    bad = dict(pilot_kit.TEMPLATE[1], prompt_text="secret", useful_corrections=3)
    issues = pilot_kit.validate(bad)
    assert any("prompt_text" in i for i in issues) and any("exceed" in i for i in issues)
    assert pilot_kit.validate({"kind": "task", "mode": "auto"})
