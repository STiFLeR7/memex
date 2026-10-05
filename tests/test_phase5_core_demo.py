"""The contributor demo runs the real core with in-memory doubles and no services."""
import asyncio


def test_core_demo_holds_the_stale_edit_and_nothing_else(tmp_path):
    from memex.evaluation.core_demo import run
    lines = []
    result = asyncio.run(run(tmp_path, out=lines.append))
    assert result == {"first": "proceed", "unrelated": "proceed", "stale": "replan", "corrections": 1}
    assert any("needs_revalidation" in line for line in lines)
