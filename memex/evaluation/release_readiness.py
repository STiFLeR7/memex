"""Goal 10 evaluation matrix and conservative release gate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from memex.evaluation.harness import (
    FAILURE,
    INVALID_RUN,
    SUCCESS,
    evaluate_paired_runs,
)


@dataclass(frozen=True)
class EvaluationCase:
    slug: str
    title: str
    description: str

    @property
    def case_id(self) -> str:
        return f"goal10-{self.slug}"


EVALUATION_CASES = (
    EvaluationCase("unfamiliar-repository", "Unfamiliar repository", "Navigate and identify the relevant subsystem."),
    EvaluationCase("architecture-investigation", "Architecture investigation", "Use repository decisions to explain an architectural change."),
    EvaluationCase("bug-investigation", "Bug investigation", "Locate the cause of a defect and its relevant history."),
    EvaluationCase("regression-fix", "Regression fix", "Fix a regression and verify the repository state."),
    EvaluationCase("multi-session", "Multi-session continuation", "Continue an engineering task across separate sessions."),
    EvaluationCase("agent-handoff", "Agent handoff", "Continue work from a bounded engineering handoff."),
    EvaluationCase("stale-knowledge", "Stale or superseded knowledge", "Recognize and correctly handle outdated context."),
    EvaluationCase("parallel-conflicts", "Parallel conflicting work", "Handle parallel changes and conflicting engineering decisions."),
)

Runner = Callable[[EvaluationCase], Mapping[str, Mapping[str, Any]]]


def _unavailable(case: EvaluationCase) -> dict[str, Any]:
    base = {
        "arm": "baseline",
        "task_id": case.case_id,
        "session_id": f"{case.case_id}-baseline",
        "task_outcome": INVALID_RUN,
    }
    treatment = {
        "arm": "treatment",
        "task_id": case.case_id,
        "session_id": f"{case.case_id}-treatment",
        "task_outcome": INVALID_RUN,
    }
    return {
        "valid": False,
        "task_id": case.case_id,
        "evidence_status": "not_executed",
        "pair_failures": ["live_agent_runner_unavailable"],
        "baseline": base,
        "treatment": treatment,
    }


def _evaluate_case(case: EvaluationCase, runner: Runner | None) -> dict[str, Any]:
    if runner is None:
        return _unavailable(case)
    try:
        runs = runner(case)
        return evaluate_paired_runs(runs["baseline"], runs["treatment"])
    except Exception as exc:
        return _unavailable(case) | {
            "pair_failures": [f"runner_failed:{type(exc).__name__}:{exc}"],
        }


def run_evaluation_matrix(
    runner: Runner | None = None,
    cases: tuple[EvaluationCase, ...] = EVALUATION_CASES,
) -> dict[str, Any]:
    """Run the eight-case matrix or report that live execution is unavailable."""

    results = [
        {"case": {"slug": case.slug, "title": case.title}, "evaluation": _evaluate_case(case, runner)}
        for case in cases
    ]
    valid_count = sum(item["evaluation"]["valid"] for item in results)
    invalid_count = len(results) - valid_count
    failure_count = sum(
        item["evaluation"].get("treatment", {}).get("status") == FAILURE
        for item in results
    )
    worse_count = sum(
        item["evaluation"].get("baseline", {}).get("status") == SUCCESS
        and item["evaluation"].get("treatment", {}).get("status") != SUCCESS
        for item in results
    )
    release_decision = "GO"
    reasons = []
    if invalid_count:
        release_decision = "NO-GO"
        reasons.append("valid paired evidence is missing")
    if failure_count:
        release_decision = "NO-GO"
        reasons.append("treatment failures are present")
    if worse_count:
        release_decision = "NO-GO"
        reasons.append("treatment does not meet baseline success")
    if runner is None:
        evidence_status = "not_executed"
    else:
        evidence_status = "paired_runs"
    return {
        "case_count": len(results),
        "valid_count": valid_count,
        "invalid_count": invalid_count,
        "failure_count": failure_count,
        "release_decision": release_decision,
        "evidence_status": evidence_status,
        "reasons": reasons,
        "cases": results,
    }
