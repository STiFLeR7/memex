"""The mechanism tier: F01-F16, each tied to executable tests on the real runtime.

usage:  python -m memex.evaluation.mechanism [--json out.json]

Runs the deterministic tests that establish each fixture's gold behavior
(real Git, SQLite and the native Neo4j fixture; no model) and reports every
fixture as passed only if all of its tests ran and passed. A skipped test is
not a pass. Native-host evidence for the same fixtures is listed separately:
it comes from the opt-in native gates and is not re-run here.
"""
from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]

FIXTURES: dict[str, dict] = {
    "F01": {"gold": "Body-only change: the dependent assumption is rechecked",
            "tests": ["tests/test_live_graph.py::test_atomic_body_change_empty_removal_and_replay",
                      "tests/test_live_actions.py::test_body_change_correction_is_scoped_and_idempotent",
                      "tests/test_phase4_shared_checkout.py::test_a_body_only_change_corrects_each_dependent_host_independently",
                      "tests/test_phase5_arms.py::test_each_arm_decides_by_its_rule_when_the_contract_changes"]},
    "F02": {"gold": "A removed last call/import stops supporting current context",
            "tests": ["tests/test_live_graph.py::test_final_import_removed_without_erasing_history",
                      "tests/test_live_graph.py::test_atomic_body_change_empty_removal_and_replay",
                      "tests/test_live_supports.py::test_structural_predicate_parse_error_and_access"]},
    "F03": {"gold": "A superseded approved decision is explicitly corrected",
            "tests": ["tests/test_live_actions.py::test_explicit_supersession_future_and_conflict",
                      "tests/test_live_claim_graph.py::test_approval_and_supersession_require_authorized_history"]},
    "F04": {"gold": "An unrelated edit causes no material interruption",
            "tests": ["tests/test_live_actions.py::test_unrelated_changes_quiet_alternative_survives_and_rule_active",
                      "tests/test_phase4_shared_checkout.py::test_an_unrelated_change_interrupts_neither_host",
                      "tests/test_phase5_arms.py::test_an_unrelated_change_interrupts_no_arm"]},
    "F05": {"gold": "A claim stays supported while a sufficient alternative survives",
            "tests": ["tests/test_live_supports.py::test_or_of_and_support_and_empty_not_truth",
                      "tests/test_live_actions.py::test_surviving_alternative_with_parse_error_is_sufficient",
                      "tests/test_phase5_arms.py::test_a_surviving_alternative_support_separates_hash_notes_from_live_context"]},
    "F06": {"gold": "A governed file change keeps the approved rule active",
            "tests": ["tests/test_live_actions.py::test_unrelated_changes_quiet_alternative_survives_and_rule_active",
                      "tests/test_live_supports.py::test_approval_test_scope_future_and_derivation_bound"]},
    "F07": {"gold": "A parse failure is unknown coverage, not certified structure",
            "tests": ["tests/test_live_graph.py::test_parse_failure_and_branch_are_not_current_structure",
                      "tests/test_live_recovery.py::test_parse_failure_does_not_certify_fresh_and_checkout_change_reindexes",
                      "tests/test_live_actions.py::test_parse_error_opaque_scope_and_missing_target_unknown"]},
    "F08": {"gold": "Two worktrees keep isolated, correct per-view context",
            "tests": ["tests/test_phase4_worktrees.py::test_same_path_different_api_gets_per_view_claims_and_no_leakage",  # noqa: E501
                      "tests/test_live_actions.py::test_two_worktrees_keep_same_claim_verification_separate"]},
    "F09": {"gold": "A merge is revalidated on the actual merged contents",
            "tests": ["tests/test_phase4_worktrees.py::test_merge_into_a_revalidates_before_the_affected_action",
                      "tests/test_phase4_worktrees.py::test_conflict_resolution_is_verified_as_its_own_view",
                      "tests/test_phase4_worktrees.py::test_cherry_pick_and_rebase_each_revalidate_the_receiving_view"]},
    "F10": {"gold": "Two hosts sharing a checkout get independent corrections and receipts",
            "tests": ["tests/test_phase4_shared_checkout.py::test_identical_native_ids_keep_two_hosts_streams_apart",
                      "tests/test_live_actions.py::test_two_clients_receive_independent_corrections_after_restart"]},
    "F11": {"gold": "A target changed between read and guarded write is refused",
            "tests": ["tests/test_phase4_guard.py::test_ordered_race_on_one_target_commits_once_and_the_loser_replans",
                      "tests/test_phase4_guard.py::test_a_paused_holder_cannot_write_after_it_is_replaced",
                      "tests/test_phase4_guard_recovery.py::test_recovery_never_replays_an_old_intent_over_a_newer_guarded_write"]},
    "F12": {"gold": "Compaction, handoff and resume re-establish the working set",
            "tests": ["tests/test_live_actions.py::test_budget_overflow_and_compaction_resync",
                      "tests/test_phase4_shared_checkout.py::test_restart_expiry_and_resume_preserve_independent_cursors"]},
    "F13": {"gold": "A missed event or restart is recovered by the action check",
            "tests": ["tests/test_live_recovery.py::test_missed_event_body_edit_revert_deletion_and_restart",
                      "tests/test_live_recovery.py::test_graph_commit_before_local_ack_recovers_without_duplicate"]},
    "F14": {"gold": "An unavailable service yields no fresh receipt and explicit fail-open coverage",
            "tests": ["tests/test_live_actions.py::test_backend_failure_returns_unknown_not_certificate",
                      "tests/test_phase4_shared_checkout.py::test_outage_failed_insertion_and_overflow_degrade_explicitly_for_both_hosts"]},
    "F15": {"gold": "A budget or propagation overflow reports resynchronization",
            "tests": ["tests/test_live_tasks.py::test_pending_delta_replay_mismatched_base_and_overflow",
                      "tests/test_phase2_review_regressions.py::test_review_128_retractions_plus_additions_resynchronize",
                      "tests/test_live_actions.py::test_dependency_bound_resync_cannot_be_acked"]},
    "F16": {"gold": "No automatic authority promotion and no future leakage",
            "tests": ["tests/test_live_actions.py::test_explicit_supersession_future_and_conflict",
                      "tests/test_phase4_worktrees.py::test_approved_rules_share_by_scope_and_proposals_never_promote",
                      "tests/test_live_supports.py::test_approval_test_scope_future_and_derivation_bound"]},
}

#: Any failure here is an isolation or authority violation, which blocks release.
ISOLATION_AND_AUTHORITY = [
    "tests/test_phase4_worktrees.py::test_same_path_different_api_gets_per_view_claims_and_no_leakage",
    "tests/test_phase4_worktrees.py::test_corrections_never_expose_another_tasks_scope",
    "tests/test_phase4_worktrees.py::test_approved_rules_share_by_scope_and_proposals_never_promote",
    "tests/test_phase4_shared_checkout.py::test_identical_native_ids_keep_two_hosts_streams_apart",
    "tests/test_live_actions.py::test_access_checks_hide_claims_and_precede_lookup",
    "tests/test_live_actions.py::test_authorization_revocation_before_cached_projection",
    "tests/test_live_actions.py::test_review_revoked_claim_does_not_bypass_source_acl",
    "tests/test_live_claim_graph.py::test_approval_and_supersession_require_authorized_history",
    "tests/test_phase5_migration.py::test_legacy_knowledge_never_enters_a_verified_packet",
    "tests/test_phase5_migration.py::test_migration_preserves_legacy_data_authority_and_history",
]

#: Native-host evidence for the same behaviors, from the opt-in gates (not re-run here).
NATIVE = {
    "F01": ["tests/test_phase3_native_loop.py", "tests/test_phase4_native_codex.py"],
    "F08": ["tests/test_phase4_native_worktrees.py"], "F09": ["tests/test_phase4_native_worktrees.py"],
    "F10": ["tests/test_phase4_native_shared_checkout.py"], "F11": ["tests/test_phase4_native_guarded.py"],
}


def run(junit_dir: Path) -> dict:
    tests = sorted({t for f in FIXTURES.values() for t in f["tests"]} | set(ISOLATION_AND_AUTHORITY))
    junit = junit_dir / "mechanism.xml"
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-W", "ignore",
                    f"--junitxml={junit}", *tests], cwd=ROOT, capture_output=True, text=True)
    outcomes: dict[str, str] = {}
    # The only input is the JUnit file pytest just wrote into our private temporary directory.
    for case in ElementTree.parse(junit).getroot().iter("testcase"):
        file = case.get("classname", "").replace(".", "/") + ".py"
        name = case.get("name", "").split("[")[0]
        state = ("failed" if case.find("failure") is not None or case.find("error") is not None
                 else "skipped" if case.find("skipped") is not None else "passed")
        key = f"{file}::{name}"
        # A parametrized test passes only if every parametrization passed.
        if outcomes.get(key) in (None, "passed"):
            outcomes[key] = state
    fixtures = {}
    for fid, spec in FIXTURES.items():
        states = {t: outcomes.get(t, "not_collected") for t in spec["tests"]}
        fixtures[fid] = {"gold": spec["gold"], "status": "passed" if all(s == "passed" for s in states.values())
                         else "failed", "tests": states, "native_evidence": NATIVE.get(fid, [])}
    isolation = {t: outcomes.get(t, "not_collected") for t in ISOLATION_AND_AUTHORITY}
    return {"fixtures": fixtures,
            "isolation_and_authority": {"status": "passed" if all(s == "passed" for s in isolation.values())
                                        else "failed", "tests": isolation},
            "passed": sum(f["status"] == "passed" for f in fixtures.values()), "total": len(fixtures)}


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    with TemporaryDirectory(prefix="memex-mechanism-") as scratch:
        result = run(Path(scratch))
    for fid, fixture in result["fixtures"].items():
        print(f"{fid} {fixture['status']:6}  {fixture['gold']}")
        for test, state in fixture["tests"].items():
            if state != "passed":
                print(f"      {state}: {test}")
    print(f"isolation and authority: {result['isolation_and_authority']['status']}")
    print(f"{result['passed']}/{result['total']} fixtures passed")
    if "--json" in argv:
        Path(argv[argv.index("--json") + 1]).write_text(json.dumps(result, indent=1))
    return 0 if result["passed"] == result["total"] and result["isolation_and_authority"]["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
