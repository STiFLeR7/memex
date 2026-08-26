"""Deterministic Goal 4 baseline-vs-treatment integration scenario.

This is a provider-contract smoke test, not a model efficacy benchmark. It
uses verified files from the memex repository and keeps the result suitable
for later joining to the Goal 5 evaluation schema.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memex.config import canonical_repo_path
from memex.context.packet import (
    ContextItem,
    ContextPacket,
    ExecutionContext,
    ProvenanceRef,
    RepositoryScope,
    SelectionMetadata,
    TaskContext,
)
from memex.integrations.hermes_provider import HermesMemexProvider

TASK_ID = "goal4-retrieval-pipeline"
RETRIEVAL_ID = "goal4-local-fixture"
FIXTURE_OBSERVED_AT = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
REQUIRED_CONTEXT_TERMS = (
    "composite_search",
    "memex/context/selection.py",
    "reranker.py",
    "Why:",
)
REQUIRED_SOURCE_MARKERS = {
    "memex/context/selection.py": "composite_search",
    "memex/mcp_server/queries.py": "composite_search",
    "memex/mcp_server/reranker.py": "class ScoredResult",
}


@dataclass(frozen=True)
class _RunResult:
    arm: str
    task_id: str
    session_id: str
    context_returned: bool
    context_chars: int
    packet_id: str | None
    retrieval_id: str | None
    selected_entities: list[str]
    selection_reasons: list[str]
    prefetch_latency_ms: float
    task_outcome: str
    useful_context: bool
    verification: list[str]
    failures: list[str]


def _fixture_packet(query: str, **kwargs: Any) -> ContextPacket:
    repo = str(kwargs["repo"])
    items = [
        ContextItem(
            item_id="goal4-selection",
            entity_type="EngineeringContext",
            ref="memex/context/selection.py",
            summary=(
                "Context selection adapts the existing composite_search retrieval "
                "path into a bounded ContextPacket."
            ),
            scope=repo,
            confidence=0.98,
            freshness="current",
            source_refs=[
                ProvenanceRef(
                    kind="derived",
                    ref="goal4-local-fixture:selection",
                    observed_at=FIXTURE_OBSERVED_AT,
                )
            ],
            selection_reason=["same repository", "query relevance", "retrieval score"],
            score_breakdown={"fixture_score": 1.0},
        ),
        ContextItem(
            item_id="goal4-retrieval",
            entity_type="RetrievalPath",
            ref="memex/mcp_server/queries.py",
            summary="The existing retrieval path exposes composite_search for repository queries.",
            scope=repo,
            confidence=0.97,
            freshness="current",
            source_refs=[
                ProvenanceRef(
                    kind="derived",
                    ref="goal4-local-fixture:retrieval",
                    observed_at=FIXTURE_OBSERVED_AT,
                )
            ],
            relationships=["memex/context/selection.py"],
            selection_reason=["same repository", "directly related"],
            score_breakdown={"fixture_score": 0.95},
        ),
        ContextItem(
            item_id="goal4-reranker",
            entity_type="Scoring",
            ref="memex/mcp_server/reranker.py",
            summary="The reranker exposes score and confidence signals for selected results.",
            scope=repo,
            confidence=0.95,
            freshness="current",
            source_refs=[
                ProvenanceRef(
                    kind="derived",
                    ref="goal4-local-fixture:reranker",
                    observed_at=FIXTURE_OBSERVED_AT,
                )
            ],
            relationships=["memex/context/selection.py"],
            selection_reason=["same repository", "supporting retrieval evidence"],
            score_breakdown={"fixture_score": 0.9},
        ),
    ]
    return ContextPacket.from_items(
        items=items,
        packet_id="packet-goal4-local",
        kind="prefetch",
        repository=RepositoryScope(repo_path=repo),
        task=TaskContext(task_id=TASK_ID, query=query),
        execution=ExecutionContext(
            session_id=kwargs.get("session_id"),
            agent_id=kwargs.get("agent_id"),
            harness=kwargs.get("harness"),
        ),
        budget=kwargs["budget"],
        selection=SelectionMetadata(
            candidate_count=len(items),
            filters={"fixture": True, "repository": repo},
        ),
        evidence={"retrieval_id": RETRIEVAL_ID},
    )


def _verify_repository(repo_root: Path) -> list[str]:
    failures = []
    for relative_path, marker in REQUIRED_SOURCE_MARKERS.items():
        path = repo_root / relative_path
        if not path.is_file():
            failures.append(f"missing:{relative_path}")
            continue
        if marker not in path.read_text(encoding="utf-8"):
            failures.append(f"marker_missing:{relative_path}:{marker}")
    return failures


def _run_task(
    *,
    arm: str,
    context: str,
    packet: ContextPacket | None,
    repo_root: Path,
    prefetch_latency_ms: float,
) -> _RunResult:
    failures = _verify_repository(repo_root)
    context_has_required_evidence = all(term in context for term in REQUIRED_CONTEXT_TERMS)
    if not context_has_required_evidence:
        failures.append("context_missing_required_evidence")

    repository_verified = not any(failure.startswith(("missing:", "marker_missing:")) for failure in failures)
    if not repository_verified:
        outcome = "FAILURE"
    elif context_has_required_evidence:
        outcome = "SUCCESS"
    else:
        outcome = "PARTIAL"

    verification = []
    if repository_verified:
        verification.append("repository_state_verified")
    if packet is not None and context_has_required_evidence:
        verification.append("context_contract_verified")

    return _RunResult(
        arm=arm,
        task_id=TASK_ID,
        session_id=f"goal4-{arm}-session",
        context_returned=bool(context),
        context_chars=len(context),
        packet_id=packet.packet_id if packet else None,
        retrieval_id=packet.evidence.retrieval_id if packet else None,
        selected_entities=[item.ref for item in packet.items] if packet else [],
        selection_reasons=[reason for item in packet.items for reason in item.selection_reason]
        if packet
        else [],
        prefetch_latency_ms=round(prefetch_latency_ms, 3),
        task_outcome=outcome,
        useful_context=packet is not None and context_has_required_evidence and not failures,
        verification=verification,
        failures=failures,
    )


def run_local_vertical_slice(repo_root: str | Path) -> dict[str, Any]:
    """Run one reproducible local baseline/treatment scenario.

    The treatment uses the real memex Hermes provider contract with a local
    deterministic selector. No graph, network, transcript, or Hermes state is
    read. The result deliberately makes no product-efficacy claim.
    """

    root = Path(canonical_repo_path(str(repo_root)) or repo_root)
    query = "Understand the current retrieval pipeline and context selection layer."

    baseline = _run_task(
        arm="baseline",
        context="",
        packet=None,
        repo_root=root,
        prefetch_latency_ms=0.0,
    )

    captured: dict[str, ContextPacket] = {}

    async def selector(query: str, **kwargs: Any) -> ContextPacket:
        packet = _fixture_packet(query, **kwargs)
        captured["packet"] = packet
        return packet

    provider = HermesMemexProvider(
        {
            "repo_path": str(root),
            "project_id": "memex-goal4",
            "prefetch_timeout_seconds": 1.0,
            "max_items": 3,
            "max_chars": 12_000,
        },
        selector=selector,
    )
    session_id = "goal4-treatment-session"
    provider.initialize(session_id, repo_path=str(root), agent_identity="goal4-harness")
    started = time.perf_counter()
    context = provider.prefetch(query, session_id=session_id)
    latency_ms = (time.perf_counter() - started) * 1000
    provider.shutdown()

    treatment = _run_task(
        arm="treatment",
        context=context,
        packet=captured.get("packet"),
        repo_root=root,
        prefetch_latency_ms=latency_ms,
    )

    return {
        "scenario": "goal4-local-retrieval-pipeline",
        "task_id": TASK_ID,
        "repository": str(root),
        "harness": "hermes-provider-contract",
        "improvement_claim": "not_established",
        "baseline": asdict(baseline),
        "treatment": asdict(treatment),
    }
