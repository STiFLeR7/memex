"""Composite reranker for memex search (Phase 7 / ARCHITECTURE-v0.3.0 §8).

memex does NOT replace Graphiti's hybrid retrieval — it composes a thin
post-rerank pass on top. This module implements:

  1. Hard filter on Graphiti's ``expired_at`` (zombie-edge fix; see §8 Step 1).
  2. Multiplicative composite over Graphiti's ``rerank_score``:

         final = rerank_score
                 * exp(-Δt_days / τ)                        # recency, τ default 90
                 * (conf_floor + (1 - conf_floor) * conf)   # confidence floor
                 * (1 + rehearsal_weight * log(1 + access_count))

     (See ARCHITECTURE §8 Step 2 and Risk 2 for the derivation. The
     multiplicative form avoids the score-normalisation pitfall of
     weighted-sum across un-normalised signals — Hsu & Taksa 2007 / Milvus
     decay-ranker / Weighted Product Model.)

  3. Reciprocal Rank Fusion (k=60) across N modality lists. Each list is
     pre-sorted by composite score; ranks are summed as ``1/(rank + k)`` and
     the merged output is re-sorted by total RRF score.

Why surface a per-factor breakdown? Per §8 "Score breakdown surfaced to
agents": multiplicative composition loses the intuitive "this term is 30%
of the score" interpretability, so the breakdown is emitted alongside each
result so agents can weigh conflicting evidence themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import exp, log
from typing import Any, Iterable, Optional

from memex.graph.confidence import current_confidence

# ---------------------------------------------------------------------------
# Constants — match memex.config.RetrievalConfig defaults. Tests pin these.
# ---------------------------------------------------------------------------

RECENCY_TAU_DAYS = 90       # τ — exponential decay timescale (days)
CONF_FLOOR = 0.5            # confidence factor floor
REHEARSAL_WEIGHT = 0.1      # access_count log coefficient
RRF_K = 60                  # Reciprocal Rank Fusion constant


# ---------------------------------------------------------------------------
# ScoredResult — wraps a Graphiti result with the composite-score breakdown
# so the formatter can render it for agent transparency (ARCHITECTURE §8).
# ---------------------------------------------------------------------------


@dataclass
class ScoredResult:
    """A single Graphiti result enriched with the multiplicative composite
    breakdown and (after ``merge_modalities``) its RRF score.

    The breakdown fields exist so the formatter can render them — they are
    not load-bearing in any downstream calculation."""

    uuid: str
    node_or_edge: Any              # original Graphiti object
    modality: str                  # "edge" | "node" | "episode" | "community"
    graphiti_score: float          # rerank_score Graphiti gave us
    recency_factor: float
    confidence_factor: float
    rehearsal_boost: float
    final_score: float
    rrf_score: float = 0.0         # populated by ``merge_modalities``
    extras: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers — mirror confidence.py's tolerant accessor pattern so we work with
# both dicts (Cypher rows) and attribute-style objects (Graphiti models).
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        val = obj.get(key, default)
    else:
        val = getattr(obj, key, default)
    if val is None:
        return default
    return val


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    to_native = getattr(value, "to_native", None)
    if callable(to_native):
        try:
            return to_native()
        except Exception:
            pass
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _resolve_anchor(result: Any) -> Optional[datetime]:
    """Pick the timestamp used for recency decay. Spec calls this
    ``valid_from``; Graphiti edges call it ``valid_at``; episodes use
    ``created_at``. Fall through gracefully."""
    for field_name in ("valid_from", "valid_at", "created_at"):
        ts = _as_datetime(_get(result, field_name))
        if ts is not None:
            return _ensure_aware(ts)
    return None


# ---------------------------------------------------------------------------
# Composite scoring
# ---------------------------------------------------------------------------


def composite_score(
    result: Any,
    now: Optional[datetime] = None,
    tau_days: int = RECENCY_TAU_DAYS,
    conf_floor: float = CONF_FLOOR,
    rehearsal_weight: float = REHEARSAL_WEIGHT,
    modality: str = "edge",
) -> Optional[ScoredResult]:
    """Apply the multiplicative composite to a single Graphiti result.

    Returns ``None`` if the result has ``expired_at`` set (hard filter per
    §8 Step 1 — Graphiti's bi-temporal invalidation should never leak into
    agent context). Otherwise returns a ``ScoredResult`` with the per-factor
    breakdown populated.
    """
    if _get(result, "expired_at") is not None:
        return None

    if now is None:
        now = _utc_now()
    if now.tzinfo is None:
        now = _ensure_aware(now)

    # Recency factor — exp(-Δt / τ). If no anchor is present treat as fresh.
    anchor = _resolve_anchor(result)
    if anchor is None:
        age_days = 0.0
    else:
        age_days = max(0.0, (now - anchor).total_seconds() / 86400.0)
    recency_factor = exp(-age_days / float(tau_days))

    # Confidence factor — floor + (1 - floor) * computed_confidence(r).
    # Configurable form so RetrievalConfig.conf_floor is wired through.
    conf = current_confidence(result)
    confidence_factor = conf_floor + (1.0 - conf_floor) * conf

    # Rehearsal boost — 1 + w * log(1 + access_count). Default w=0.1.
    access = int(_get(result, "access_count", 0) or 0)
    if access < 0:
        access = 0
    rehearsal_boost = 1.0 + rehearsal_weight * log(1.0 + access)

    # Graphiti hands us a rerank_score per result. v0.2.0 results used
    # ``score``; fall back to that, then to 1.0 (so a missing score doesn't
    # collapse the entire composite to 0).
    graphiti_score = float(_get(result, "rerank_score", _get(result, "score", 1.0)) or 1.0)

    final = graphiti_score * recency_factor * confidence_factor * rehearsal_boost

    uuid = str(_get(result, "uuid", _get(result, "id", "")) or "")

    return ScoredResult(
        uuid=uuid,
        node_or_edge=result,
        modality=modality,
        graphiti_score=graphiti_score,
        recency_factor=recency_factor,
        confidence_factor=confidence_factor,
        rehearsal_boost=rehearsal_boost,
        final_score=final,
    )


def score_and_rank(
    results: Iterable[Any],
    now: Optional[datetime] = None,
    tau_days: int = RECENCY_TAU_DAYS,
    conf_floor: float = CONF_FLOOR,
    rehearsal_weight: float = REHEARSAL_WEIGHT,
    modality: str = "edge",
) -> list[ScoredResult]:
    """Score a single modality list and return it sorted by final_score
    (descending). Drops expired results."""
    scored: list[ScoredResult] = []
    for r in results:
        s = composite_score(
            r,
            now=now,
            tau_days=tau_days,
            conf_floor=conf_floor,
            rehearsal_weight=rehearsal_weight,
            modality=modality,
        )
        if s is not None:
            scored.append(s)
    scored.sort(key=lambda sr: sr.final_score, reverse=True)
    return scored


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion across modality lists
# ---------------------------------------------------------------------------


def merge_modalities(*lists: list[ScoredResult], k: int = RRF_K) -> list[ScoredResult]:
    """RRF(k) merge of N modality lists.

    Each input list must already be sorted by composite ``final_score``
    (descending) — call ``score_and_rank`` first. Each result's ``rrf_score``
    is the sum of ``1 / (rank + k)`` across the lists it appears in (rank is
    0-indexed). The merged output is sorted by total RRF score (descending).

    A given uuid appearing in multiple lists keeps the FIRST ``ScoredResult``
    instance encountered (caller can inspect ``rrf_score`` for the fusion
    contribution). Lists with disjoint uuids degrade gracefully — each
    result's rrf_score is simply ``1 / (rank + k)``.

    Why k=60: OpenSearch / Azure AI Search / Chroma all converge on this
    default for heterogeneous fusion. Cormack et al. (2009) original.
    """
    rrf_scores: dict[str, float] = {}
    first_seen: dict[str, ScoredResult] = {}

    for lst in lists:
        for rank, sr in enumerate(lst):
            if not sr.uuid:
                # Skip entries with no stable identifier — they can't be
                # de-duplicated across lists.
                continue
            rrf_scores[sr.uuid] = rrf_scores.get(sr.uuid, 0.0) + 1.0 / (rank + k)
            if sr.uuid not in first_seen:
                first_seen[sr.uuid] = sr

    merged: list[ScoredResult] = []
    for uuid, total in rrf_scores.items():
        sr = first_seen[uuid]
        sr.rrf_score = total
        merged.append(sr)

    merged.sort(key=lambda sr: sr.rrf_score, reverse=True)
    return merged
