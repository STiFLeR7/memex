"""W16 analysis: definitions, clustered bootstrap and gate decisions on synthetic records."""
import pytest

from memex.evaluation import analysis


def trial(history, arm, host="claude", *, label="affected", stale=False, success=None, denied=(), necessary=None,
          status="completed", template="t1", hooks=()):
    necessary = necessary if necessary is not None else [label == "affected"] * max(1, len(denied))
    marks = list(denied) or [False]
    return {"history": history, "arm": arm, "host": host, "label": label, "template": template, "status": status,
            "stale_failure": stale, "success": (not stale) if success is None else success,
            "functional_failure": False,
            "boundaries": [{"necessary": n, "denied": d, "deferred_correction": False}
                           for n, d in zip(necessary, marks, strict=True)],
            "hooks": list(hooks), "client": {"wall_s": 10, "usage": {"output_tokens": 100, "cost_usd": 0.05}}}


def test_correction_metrics_follow_the_written_definitions():
    records = [
        trial("H1", "E", denied=[True, False], necessary=[True, True]),         # timely, material
        trial("H2", "E", denied=[False, True], necessary=[False, True]),        # pre-change boundary quiet; not timely?
        trial("H3", "E", label="stable", denied=[True], necessary=[False]),     # false interruption
        trial("H4", "E", label="stable", denied=[False, False], necessary=[False, False]),
    ]
    summary = analysis.summarize(records, "E")
    assert summary["precision"] == {"n": 2, "d": 3, "rate": 2 / 3}
    assert summary["interception"] == {"n": 2, "d": 2, "rate": 1.0}  # H2's first necessary boundary was denied
    assert summary["false_interruption"] == {"n": 1, "d": 4, "rate": 0.25}
    assert summary["retries"] == 1


def test_failed_and_unavailable_trials_stay_in_the_counts_and_out_of_outcomes():
    records = [trial("H1", "E", stale=True), trial("H2", "E", status="timeout"), trial("H3", "E", status="unavailable")]
    summary = analysis.summarize(records, "E")
    assert summary["trials"] == 3 and summary["status"] == {"completed": 1, "timeout": 1, "unavailable": 1}
    assert summary["stale_failure"] == {"n": 1, "d": 1, "rate": 1.0}


def test_missing_usage_is_unknown_not_zero():
    record = trial("H1", "E")
    record["client"]["usage"] = None
    summary = analysis.summarize([record, trial("H2", "E")], "E")
    assert summary["usage_known"] == "1/2" and summary["cost_known"] == "1/2" and summary["output_tokens_mean"] == 100


def test_latency_uses_proceeding_declared_checks_only():
    hooks = [{"event": "PreToolUse", "tool": "Edit", "decision": None, "total_ms": t, "check_ms": t / 2}
             for t in (100, 150, 190, 900)]
    hooks += [{"event": "PreToolUse", "tool": "Edit", "decision": "deny", "total_ms": 5000},
              {"event": "PreToolUse", "tool": "Bash", "decision": None, "total_ms": 5000},
              {"event": "SessionStart", "tool": None, "decision": None, "total_ms": 5000}]
    summary = analysis.summarize([trial("H1", "E", hooks=hooks)], "E")
    assert summary["latency_warm_unchanged_ms"]["n"] == 4 and summary["latency_warm_unchanged_ms"]["p95"] == 900
    assert analysis.latency_gate(summary, 200)["status"] == "failed"


def test_strongest_baseline_and_paired_bootstrap():
    records = []
    for i in range(20):
        h = f"H{i:02d}"
        records += [trial(h, "E", stale=i < 2), trial(h, "C", stale=i < 10), trial(h, "D", stale=i < 6)]
    units = analysis.paired_units(records, ("E", "C", "D"))
    assert analysis.strongest_baseline(units) == "D"
    result = analysis.bootstrap(units, "E", "D", resamples=2000)
    assert result["p_primary"] == 0.1 and result["p_baseline"] == 0.3
    assert result["relative_reduction"] == pytest.approx(2 / 3)
    low, high = result["difference_ci95"]
    assert 0 < low < result["difference"] < high
    frozen = {"resamples": 2000, "seed": 1, "min_relative_reduction": 0.30}
    assert analysis.efficacy_gate(records, frozen)["status"] == "passed"


def test_no_reduction_is_possible_when_the_baseline_never_fails():
    records = [trial(f"H{i}", arm, stale=False) for i in range(10) for arm in ("C", "D", "E")]
    decision = analysis.efficacy_gate(records, {"resamples": 500, "seed": 1, "min_relative_reduction": 0.30})
    assert decision["status"] == "failed" and "no stale-context failures" in decision["detail"]


def test_an_incomplete_pair_is_not_a_unit():
    records = [trial("H1", "E"), trial("H1", "C"), trial("H1", "D", status="timeout")]
    assert analysis.paired_units(records, ("E", "C", "D")) == {}
    assert analysis.efficacy_gate(records, {"resamples": 10, "seed": 1, "min_relative_reduction": 0.3})["status"] \
        == "not_established"


def test_sample_size_is_undefined_without_an_expected_difference():
    assert analysis.required_units(0.0, 0.0) is None
    assert analysis.required_units(0.1, 0.1) is None
    small, large = analysis.required_units(0.4, 0.1), analysis.required_units(0.2, 0.14)
    assert 10 < small < large


def test_stable_noninferiority_is_paired_by_history():
    records = []
    for i in range(10):
        records += [trial(f"S{i}", "E", label="stable", success=i != 0, necessary=[False]),
                    trial(f"S{i}", "A", label="stable", success=True, necessary=[False])]
    result = analysis.stable_noninferiority(records, "E", "A", resamples=1000)
    assert result["difference"] == pytest.approx(-0.1) and result["difference_ci95"][1] <= 0


def test_noninferiority_tail_and_resource_gates():
    frozen = {"resamples": 1000, "noninferiority_margin": 0.10}
    records = []
    for i in range(10):
        records += [trial(f"S{i}", "E", label="stable", necessary=[False]),
                    trial(f"S{i}", "A", label="stable", success=i != 0, necessary=[False])]
    assert analysis.noninferiority_gate(records, "A", frozen)["status"] == "passed"
    worse = [dict(r, success=False) if r["arm"] == "E" and r["history"] in ("S1", "S2", "S3") else r
             for r in records]
    assert analysis.noninferiority_gate(worse, "A", frozen)["status"] == "failed"

    slow = {"event": "PreToolUse", "tool": "Edit", "decision": "deny", "total_ms": 12000}
    fast = {"event": "PreToolUse", "tool": "Edit", "decision": None, "total_ms": 900}
    assert analysis.tail_gate([trial("H1", "E", hooks=[fast])], 10000)["status"] == "passed"
    assert analysis.tail_gate([trial("H1", "E", hooks=[fast, slow])], 10000)["status"] == "failed"

    e, d = trial("H1", "E"), trial("H1", "D")
    assert analysis.resource_gate([e, d], "D", 1.25)["status"] == "passed"
    e["client"]["wall_s"] = 20
    assert analysis.resource_gate([e, d], "D", 1.25)["status"] == "failed"
    e["client"] = {"wall_s": None, "usage": None}
    decision = analysis.resource_gate([e, d], "D", 1.25)
    assert decision["status"] == "not_established" and "0/1" not in decision["detail"]
