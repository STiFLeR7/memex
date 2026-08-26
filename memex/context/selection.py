"""Thin context selection over memex's existing retrieval contract.

This module adapts ``composite_search`` results into the bounded packet model.
It does not introduce a second ranking engine.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable

from memex.context.packet import (
    ContextItem,
    ContextPacket,
    PacketBudget,
    ProvenanceRef,
)
from memex.graph.confidence import current_confidence
from memex.mcp_server.queries import composite_search


Retriever = Callable[..., Awaitable[Iterable[Any]]]
_PROVENANCE_KINDS = {"watcher", "commit", "agent", "human", "import", "derived"}


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    direct = getattr(value, key, None)
    if direct is not None:
        return direct
    attributes = getattr(value, "attributes", None)
    if isinstance(attributes, dict):
        return attributes.get(key, default)
    return default


def _unwrap(result: Any) -> tuple[Any, Any]:
    node = _get(result, "node_or_edge", result)
    return result, node


def _datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    to_native = getattr(value, "to_native", None)
    if callable(to_native):
        try:
            return _datetime(to_native())
        except Exception:
            return None
    if isinstance(value, str):
        try:
            return _datetime(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _scope_value(node: Any, key: str) -> Any:
    value = _get(node, key)
    if value is not None:
        return value
    scope = _get(node, "scope")
    return _get(scope, key) if isinstance(scope, dict) else None


def _matches_scope(node: Any, *, repo: str | None, project: str | None) -> bool:
    if project:
        return _scope_value(node, "project_id") == project
    if repo:
        return _scope_value(node, "repo_path") == repo
    return False


def _ref(node: Any, scored: Any) -> str | None:
    value = _get(scored, "uuid") or _get(node, "uuid") or _get(node, "id")
    return str(value) if value else None


def _summary(node: Any) -> str | None:
    for key in ("text", "fact", "summary", "description", "name"):
        value = _get(node, key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _confidence(node: Any) -> tuple[float, bool]:
    if _get(node, "base_confidence") is not None:
        return current_confidence(node), True
    stored = _get(node, "confidence")
    if stored is not None:
        return max(0.0, min(1.0, float(stored))), True
    return current_confidence(node), False


def _provenance(node: Any, ref: str, observed_at: datetime | None) -> list[ProvenanceRef]:
    refs: list[ProvenanceRef] = []
    for source in _get(node, "source_refs", []) or []:
        if isinstance(source, ProvenanceRef):
            refs.append(
                source
                if source.observed_at is not None or observed_at is None
                else source.model_copy(update={"observed_at": observed_at})
            )
        elif isinstance(source, dict):
            parsed = ProvenanceRef.model_validate(source)
            refs.append(
                parsed
                if parsed.observed_at is not None or observed_at is None
                else parsed.model_copy(update={"observed_at": observed_at})
            )
        elif str(source).strip():
            refs.append(ProvenanceRef(kind="derived", ref=str(source).strip(), observed_at=observed_at))

    commit = _get(node, "source_commit") or _get(node, "commit")
    if commit:
        refs.append(ProvenanceRef(kind="commit", ref=str(commit), observed_at=observed_at))

    source = _get(node, "source") or _get(node, "surfaced_by")
    if source and str(source).strip() not in {item.ref for item in refs}:
        kind = str(_get(node, "source_kind", "derived"))
        if kind not in _PROVENANCE_KINDS:
            kind = "derived"
        refs.append(ProvenanceRef(kind=kind, ref=str(source).strip(), observed_at=observed_at))

    return refs


def _score_breakdown(scored: Any, node: Any) -> dict[str, float]:
    fields = (
        "graphiti_score",
        "recency_factor",
        "confidence_factor",
        "rehearsal_boost",
        "final_score",
        "rrf_score",
    )
    breakdown = {
        field: float(_get(scored, field))
        for field in fields
        if _get(scored, field) is not None
    }
    if not breakdown:
        score = _get(node, "rerank_score", _get(node, "score"))
        if score is not None:
            breakdown["retrieval_score"] = float(score)
    return breakdown


def _freshness(node: Any) -> str:
    if _get(node, "conflicted") or _get(node, "conflict") or _get(node, "contradicted"):
        return "conflicted"
    if _get(node, "superseded_by") or _get(node, "superseded"):
        return "superseded"
    if _get(node, "stale"):
        return "stale"
    explicit = _get(node, "freshness")
    if explicit in {"current", "aging", "stale", "superseded", "conflicted", "unknown"}:
        return explicit
    return "current"


def _reasons(
    node: Any,
    *,
    repo: str | None,
    project: str | None,
    freshness: str,
    confidence: float,
    confidence_known: bool,
    scored: Any,
) -> list[str]:
    reasons = ["query relevance"]
    if project:
        reasons.append("same project")
    elif repo:
        reasons.append("same repository")
    if _get(node, "relationships") or _get(node, "related_to"):
        reasons.append("relationship metadata")
    if _score_breakdown(scored, node):
        reasons.append("retrieval score")
    if freshness == "stale" or freshness == "aging":
        reasons.append("stale or aging")
    elif freshness == "superseded":
        reasons.append("superseded historical knowledge")
    elif freshness == "conflicted":
        reasons.append("conflicting evidence")
    else:
        reasons.append("fresh/current")
    if not confidence_known:
        reasons.append("confidence unavailable")
    elif confidence >= 0.7:
        reasons.append("high confidence")
    elif confidence < 0.3:
        reasons.append("low confidence")
    else:
        reasons.append("moderate confidence")
    return reasons


def _item(
    result: Any,
    *,
    repo: str | None,
    project: str | None,
    allow_historical: bool,
) -> tuple[ContextItem | None, str | None]:
    scored, node = _unwrap(result)
    ref = _ref(node, scored)
    if not ref:
        return None, None
    if not _matches_scope(node, repo=repo, project=project):
        return None, ref
    if _get(node, "expired_at") is not None:
        return None, ref

    valid_until = _datetime(_get(node, "valid_until"))
    if valid_until and valid_until <= datetime.now(timezone.utc):
        return None, ref

    freshness = _freshness(node)
    if freshness == "superseded" and not allow_historical:
        return None, ref

    summary = _summary(node)
    if not summary:
        return None, ref
    confidence, confidence_known = _confidence(node)
    observed_at = _datetime(_get(node, "observed_at") or _get(node, "created_at"))
    valid_from = _datetime(_get(node, "valid_from") or _get(node, "valid_at"))
    superseded_by = _values(_get(node, "superseded_by"))
    relationships = _values(_get(node, "relationships") or _get(node, "related_to"))
    if not relationships:
        relationships = _values(_get(node, "module") or _get(node, "file"))
    source_refs = _provenance(node, ref, observed_at)
    if not source_refs:
        return None, ref

    return ContextItem(
        item_id=ref,
        entity_type=str(_get(node, "entity_type") or _get(node, "type") or "Knowledge"),
        ref=ref,
        summary=summary,
        scope=str(_scope_value(node, "repo_path") or _scope_value(node, "project_id") or repo or project),
        confidence=confidence,
        freshness=freshness,
        valid_from=valid_from,
        valid_until=valid_until,
        superseded_by=superseded_by,
        source_refs=source_refs,
        relationships=relationships,
        selection_reason=_reasons(
            node,
            repo=repo,
            project=project,
            freshness=freshness,
            confidence=confidence,
            confidence_known=confidence_known,
            scored=scored,
        ),
        score_breakdown=_score_breakdown(scored, node),
    ), None


async def select_context(
    query: str,
    *,
    repo: str | None = None,
    project: str | None = None,
    task_id: str | None = None,
    session_id: str | None = None,
    agent_id: str | None = None,
    harness: str | None = None,
    budget: PacketBudget | dict[str, Any] | None = None,
    allow_historical: bool = False,
    retrieval_id: str | None = None,
    retriever: Retriever | None = None,
) -> ContextPacket:
    """Select bounded engineering context using existing retrieval results."""
    if not repo and not project:
        raise ValueError("context selection requires repo or project scope")

    packet_budget = PacketBudget.model_validate(budget or {})
    retrieve = retriever or composite_search
    candidates = list(
        await retrieve(
            query,
            num_results=packet_budget.max_items,
            repo=repo,
            project=project,
        )
        or []
    )

    selected: list[ContextItem] = []
    rejected: list[str] = []
    for result in candidates:
        item, rejected_ref = _item(
            result,
            repo=repo,
            project=project,
            allow_historical=allow_historical,
        )
        if item is not None:
            selected.append(item)
        elif rejected_ref:
            rejected.append(rejected_ref)

    packet = ContextPacket.from_items(
        items=selected,
        repository={"repo_path": repo, "project_id": project},
        task={"task_id": task_id, "query": query},
        execution={"session_id": session_id, "agent_id": agent_id, "harness": harness},
        budget=packet_budget,
        selection={
            "filters": {"repo": repo, "project": project},
            "candidate_count": len(candidates),
            "ranking_version": "existing-composite-search-v1",
        },
        evidence={"retrieval_id": retrieval_id},
        policy={"allow_historical": allow_historical},
    )
    dropped = list(dict.fromkeys([*rejected, *packet.projection.dropped_items]))
    if dropped:
        packet = packet.model_copy(
            update={
                "projection": packet.projection.model_copy(update={"dropped_items": dropped}),
            }
        )
    return packet
