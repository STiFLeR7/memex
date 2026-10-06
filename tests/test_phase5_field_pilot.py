"""Agent-native field pilot: automatic crossover, counts-only report, amended adoption gate."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from memex.evaluation.pilot_kit import summarize_reports
from memex.runtime import pilot
from memex.runtime.migration import rollback, set_mode
from memex.runtime.modes import WEEK, read_mode
from memex.runtime.trace import TraceStore
from memex.runtime.views import discover_repository

T0 = 1_800_000_000.0


def git(repo, *args, date=None):
    env = None
    if date is not None:
        import os
        env = {**os.environ, "GIT_AUTHOR_DATE": f"{int(date)} +0000", "GIT_COMMITTER_DATE": f"{int(date)} +0000"}
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=p", "-c", "user.email=p@x", *args],
                   check=True, capture_output=True, env=env)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    (root / "a.py").write_text("x = 1\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "base", date=T0 - 100)
    return discover_repository(root)


def test_the_schedule_sets_the_mode_each_week_and_off_always_wins(repo):
    state = pilot.start(repo, "P1", weeks=4, seed=7, now=T0)
    first, second = state["schedule"][:2]
    assert {first, second} == {"live", "shadow"} and state["schedule"] == [first, second, first, second]
    assert read_mode(repo, now=T0 + 1) == first
    assert read_mode(repo, now=T0 + WEEK + 1) == second
    assert read_mode(repo, now=T0 + 4 * WEEK + 1) == "live"          # after the pilot: the worktree's own mode
    set_mode(repo, "shadow")
    assert read_mode(repo, now=T0 + 1) == first                      # the schedule drives, not a manual switch
    rollback(repo)
    assert read_mode(repo, now=T0 + 1) == "off"                       # opting out always wins
    with pytest.raises(ValueError):
        pilot.start(repo, "P1", now=T0)                               # one pilot at a time


def test_stopping_returns_the_worktree_to_its_own_mode(repo):
    pilot.start(repo, "P1", weeks=2, seed=1, now=T0)
    pilot.stop(repo, now=T0 + 10)
    assert read_mode(repo, now=T0 + 11) == "live"


def test_the_report_is_counts_only_per_week(repo):
    state = pilot.start(repo, "P2", weeks=2, seed=3, now=T0)
    trace = TraceStore(repo.runtime_path)
    s = SimpleNamespace(harness="claude-code", native_session_id="sess-1", memex_session_id="m1")
    secret = "validate.py changed: delivered sha256:aa"
    trace.record(at=T0 + 10, session=s, event="session_start", adapter_version="v")
    trace.record(at=T0 + 20, session=s, event="delivery", adapter_version="v", insertion="confirmed")
    trace.record(at=T0 + 30, session=s, event="action_check", adapter_version="v", attempt_id="att-1",
                 tool_name="Edit", gate="prevented", reason=secret)
    trace.mark_reconsidered("att-1")
    trace.record(at=T0 + 40, session=s, event="action_check", adapter_version="v", attempt_id="att-2",
                 tool_name="Edit", gate="allowed")
    s2 = SimpleNamespace(harness="codex", native_session_id="sess-2", memex_session_id="m2")
    trace.record(at=T0 + WEEK + 10, session=s2, event="session_start", adapter_version="v")
    trace.record(at=T0 + WEEK + 20, session=s2, event="shadow", adapter_version="v", attempt_id="att-3",
                 tool_name="apply_patch", gate="shadow_would_prevent", reason=secret)
    trace.record(at=T0 - 5, session=s, event="session_start", adapter_version="v")   # before the pilot: ignored
    git(repo.root, "commit", "-q", "--allow-empty", "-m", "work", date=T0 + 100)
    git(repo.root, "commit", "-q", "--allow-empty", "-m", 'Revert "work"', date=T0 + 200)

    listed = pilot.corrections(repo)
    assert [c["mode"] for c in listed] == ["live", "shadow"]
    pilot.label(repo, "att-1", "useful")
    with pytest.raises(ValueError):
        pilot.label(repo, "not-a-correction", "useful")
    pilot.checkin(repo, True, now=T0 + WEEK + 30)

    report = pilot.report(repo, now=T0 + 2 * WEEK + 1)
    w1, w2 = report["weeks"]
    assert (w1["mode"], w2["mode"]) == tuple(state["schedule"])
    assert w1["sessions"] == 1 and w1["packets_confirmed"] == 1 and w1["action_checks"] == 2
    assert w1["corrections"] == 1 and w1["reconsidered"] == 1 and w1["labeled_useful"] == 1
    assert w1["commits"] == 2 and w1["reverts"] == 1 and w1["hosts"] == ["claude-code"]
    assert w2["corrections"] == 1 and w2["unlabeled"] == 1 and w2["hosts"] == ["codex"]
    assert report["weeks_observed"] == 2 and report["sessions"] == 2
    text = json.dumps(report)
    for leaked in ("validate.py", "sha256", "att-1", "Edit", "apply_patch", str(repo.root)):
        assert leaked not in text, leaked


def test_an_unfinished_week_is_not_counted_as_observed(repo):
    pilot.start(repo, "P3", weeks=4, seed=5, now=T0)
    report = pilot.report(repo, now=T0 + WEEK + 3600)
    assert report["weeks_observed"] == 1 and len(report["weeks"]) == 2 and not report["weeks"][1]["complete"]


def _report(participant, weeks, sessions, still=True):
    week = {"week": 1, "mode": "live", "complete": True, "hosts": [], "sessions": sessions, "packets_confirmed": 0,
            "action_checks": 0, "unverified_checks": 0, "corrections": 1, "reconsidered": 1, "labeled_useful": 1,
            "labeled_false": 0, "unlabeled": 0, "commits": None, "reverts": None}
    return {"kind": "pilot-report", "version": "v", "participant": participant, "weeks_planned": 4,
            "weeks_observed": weeks, "sessions": sessions, "stopped": False,
            "checkins": [{"week": weeks, "still_using": still}], "weeks": [week]}


def test_the_amended_gate_needs_three_qualifying_independent_installations():
    two = [_report("P1", 4, 10), _report("P2", 1, 60)]
    assert summarize_reports(two)["evidence_status"] == "pending"
    three = two + [_report("P3", 4, 5, still=False)]
    summary = summarize_reports(three)
    assert summary["evidence_status"] == "reported" and summary["qualifying"] == 3
    assert summary["still_using_at_last_checkin"] == "2/3"
    assert summary["live"]["commits"] is None and summary["live"]["commits_known"] == "0/3"   # unknown, not zero
    short = three[:2] + [_report("P3", 2, 20)]
    assert summarize_reports(short)["evidence_status"] == "pending"
    leaky = dict(_report("P4", 4, 60), reasons=["validate.py changed"])
    with pytest.raises(ValueError):
        summarize_reports([leaky])
