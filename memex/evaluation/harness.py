"""Minimum paired evaluation contract for Goal 5.

The harness normalizes a baseline/treatment result and validates the trace
join. It does not run a model or claim efficacy; callers supply run results.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Mapping

from memex.context.packet import ProvenanceRef, RepositoryScope
from memex.context.task_outcome import OutcomeRecord, TaskRecord
from memex.evaluation.local_vertical_slice import run_local_vertical_slice

SUCCESS = "SUCCESS"
PARTIAL = "PARTIAL"
FAILURE = "FAILURE"
INVALID_RUN = "INVALID_RUN"
RunStatus = Literal["SUCCESS", "PARTIAL", "FAILURE", "INVALID_RUN"]
_STATUSES = frozenset((SUCCESS, PARTIAL, FAILURE, INVALID_RUN))


@dataclass(frozen=True)
class EvaluationRun:
    arm: str
    task_id: str
    session_id: str
    status: RunStatus
    outcome_id: str | None
    packet_id: str | None
    retrieval_id: str | None
    context_returned: bool
    context_chars: int
    prefetch_latency_ms: float
    selected_entities: list[str]
    useful_context: bool
    stale_context: bool
    irrelevant_context: bool
    tool_calls: int
    token_count: int
    human_intervention: bool
    verification: list[str]
    failures: list[str]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EvaluationRun":
        failures = list(value.get("failures") or [])
        validation_failures: list[str] = []
        arm = str(value.get("arm") or "")
        task_id = str(value.get("task_id") or "")
        session_id = str(value.get("session_id") or "")
        raw_status = value.get("task_outcome")
        status = raw_status if raw_status in _STATUSES else INVALID_RUN
        for name, text in (("arm", arm), ("task_id", task_id), ("session_id", session_id)):
            if not text:
                validation_failures.append(f"missing:{name}")
        if status == INVALID_RUN and raw_status not in (None, INVALID_RUN):
            validation_failures.append("invalid:task_outcome")

        packet_id = value.get("packet_id")
        retrieval_id = value.get("retrieval_id")
        context_returned = bool(value.get("context_returned", False))
        verification = list(value.get("verification") or [])
        if context_returned and (not packet_id or not retrieval_id):
            validation_failures.append("missing:context_trace")
        if status == SUCCESS and not verification:
            validation_failures.append("missing:verification")

        context_chars = _non_negative_int(value.get("context_chars", 0), "context_chars", validation_failures)
        tool_calls = _non_negative_int(value.get("tool_calls", 0), "tool_calls", validation_failures)
        token_count = _non_negative_int(value.get("token_count", 0), "token_count", validation_failures)
        latency = _non_negative_float(value.get("prefetch_latency_ms", 0.0), "prefetch_latency_ms", validation_failures)
        failures.extend(validation_failures)
        if validation_failures:
            status = INVALID_RUN

        outcome_id = value.get("outcome_id") or (f"outcome-{arm}-{task_id}" if task_id else None)
        return cls(
            arm=arm,
            task_id=task_id,
            session_id=session_id,
            status=status,
            outcome_id=outcome_id,
            packet_id=packet_id,
            retrieval_id=retrieval_id,
            context_returned=context_returned,
            context_chars=context_chars,
            prefetch_latency_ms=latency,
            selected_entities=list(value.get("selected_entities") or []),
            useful_context=bool(value.get("useful_context", False)),
            stale_context=bool(value.get("stale_context", False)),
            irrelevant_context=bool(value.get("irrelevant_context", False)),
            tool_calls=tool_calls,
            token_count=token_count,
            human_intervention=bool(value.get("human_intervention", False)),
            verification=verification,
            failures=failures,
        )


def _non_negative_int(value: Any, name: str, failures: list[str]) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        failures.append(f"invalid:{name}")
        return 0
    if number < 0:
        failures.append(f"invalid:{name}")
        return 0
    return number


def _non_negative_float(value: Any, name: str, failures: list[str]) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        failures.append(f"invalid:{name}")
        return 0.0
    if number < 0:
        failures.append(f"invalid:{name}")
        return 0.0
    return number


def evaluate_paired_runs(
    baseline: Mapping[str, Any], treatment: Mapping[str, Any]
) -> dict[str, Any]:
    """Normalize two runs and validate the minimum paired trace contract."""

    baseline_run = EvaluationRun.from_mapping(baseline)
    treatment_run = EvaluationRun.from_mapping(treatment)
    pair_failures = []
    if baseline_run.arm != "baseline":
        pair_failures.append("invalid:baseline_arm")
    if treatment_run.arm != "treatment":
        pair_failures.append("invalid:treatment_arm")
    if baseline_run.task_id != treatment_run.task_id:
        pair_failures.append("mismatch:task_id")
    valid = (
        not pair_failures
        and baseline_run.status != INVALID_RUN
        and treatment_run.status != INVALID_RUN
    )
    return {
        "valid": valid,
        "task_id": baseline_run.task_id if baseline_run.task_id == treatment_run.task_id else None,
        "evidence_status": "descriptive_only",
        "task_success_delta": (
            int(treatment_run.status == SUCCESS) - int(baseline_run.status == SUCCESS)
            if valid
            else None
        ),
        "pair_failures": pair_failures,
        "baseline": asdict(baseline_run),
        "treatment": asdict(treatment_run),
    }


def evaluate_local_vertical_slice(repo_root: str) -> dict[str, Any]:
    """Run and normalize the deterministic Goal 4 scenario."""

    report = run_local_vertical_slice(repo_root)
    evaluation = evaluate_paired_runs(report["baseline"], report["treatment"])
    recorded_at = datetime.now(timezone.utc)
    task = TaskRecord(
        task_id=report["task_id"],
        repository=RepositoryScope(repo_path=report["repository"]),
        normalized_intent="Understand the current retrieval pipeline and context selection layer.",
        scope="retrieval",
        acceptance_criteria=["Identify the retrieval entrypoint and selection layer."],
        status="completed",
        created_at=recorded_at,
        closed_at=recorded_at,
        source_refs=[
            ProvenanceRef(
                kind="derived",
                ref=f"evaluation:{report['task_id']}",
                observed_at=recorded_at,
            )
        ],
    )
    evaluation["task"] = task.model_dump(mode="json")
    evaluation["outcomes"] = {
        arm: _outcome_from_run(evaluation[arm], recorded_at).model_dump(mode="json")
        for arm in ("baseline", "treatment")
    }
    return evaluation


def _outcome_from_run(run: Mapping[str, Any], recorded_at: datetime) -> OutcomeRecord:
    run_status = run.get("task_outcome", run.get("status"))
    status = {
        SUCCESS: "success",
        PARTIAL: "partial",
        FAILURE: "failed",
        INVALID_RUN: "unknown",
    }[run_status]
    checks = list(run.get("verification") or [])
    verification_refs = [
        ProvenanceRef(kind="derived", ref=f"verification:{check}", observed_at=recorded_at)
        for check in checks
    ]
    failures = list(run.get("failures") or [])
    summary = f"{status} evaluation run"
    if failures:
        summary += f"; failures: {', '.join(failures)}"
    return OutcomeRecord(
        outcome_id=run["outcome_id"],
        task_id=run["task_id"],
        session_id=run["session_id"],
        packet_id=run.get("packet_id"),
        status=status,
        verification_refs=verification_refs,
        checks=checks,
        human_intervention=bool(run.get("human_intervention", False)),
        recorded_at=recorded_at,
        summary=summary,
    )
