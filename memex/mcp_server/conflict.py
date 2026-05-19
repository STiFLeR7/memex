"""Conflict detection for Decision nodes (Phase 7 / ARCHITECTURE-v0.3.0 §8).

Runs during ``get_recent_decisions()`` (and any other path that returns a
batch of Decision dicts). Two pieces from §8:

  1. **Bi-temporal hard signal** — handled upstream by the ``expired_at``
     filter in queries.py. Expired decisions never reach this module.
  2. **Semantic conflict signal** — for each pair of un-expired Decisions
     in the SAME module with OVERLAPPING validity windows, run a semantic
     similarity check. If similarity falls below the configured threshold
     (default 0.4 per ARCHITECTURE §8), flag BOTH decisions with
     ``conflict=True`` so the agent can call ``invalidate_edge()`` or
     ``record_decision(supersedes=...)`` to resolve it.

Why similarity-as-a-callable injection: ``graphiti_core`` does not expose a
public ``client.similarity(a, b)`` method — Graphiti runs similarity inside
``add_episode`` at threshold 0.6 but doesn't surface it via the public API.
We accept a similarity function as an optional parameter so tests can stub
it and production wiring can route through whatever embedder is configured.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# Mirrors RetrievalConfig.conflict_similarity_threshold. Below this + same
# module + overlapping validity = flagged conflict (ARCHITECTURE §8).
DEFAULT_CONFLICT_SIMILARITY_THRESHOLD = 0.4


SimilarityFn = Callable[[str, str], Awaitable[float]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    to_native = getattr(value, "to_native", None)
    if callable(to_native):
        try:
            dt = to_native()
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _validity_overlap(d1: dict, d2: dict) -> bool:
    """Two validity windows overlap iff
        start1 <= end2  AND  start2 <= end1
    Treat missing ``valid_from`` / ``valid_at`` as "always valid from
    -infinity" and missing ``valid_until`` / ``invalid_at`` as "still valid".
    """
    def _window(d: dict) -> tuple[Optional[datetime], Optional[datetime]]:
        start = (
            _as_datetime(d.get("valid_from"))
            or _as_datetime(d.get("valid_at"))
            or _as_datetime(d.get("created_at"))
        )
        end = (
            _as_datetime(d.get("valid_until"))
            or _as_datetime(d.get("invalid_at"))
        )
        return start, end

    s1, e1 = _window(d1)
    s2, e2 = _window(d2)

    # If a window has no end, treat as still-valid (open-ended on the right).
    # Overlap test: start1 <= end2 AND start2 <= end1.
    if s1 is None and s2 is None:
        # No anchors at all — assume both valid forever, so they overlap.
        return True

    # start1 <= end2
    if s1 is not None and e2 is not None and s1 > e2:
        return False
    # start2 <= end1
    if s2 is not None and e1 is not None and s2 > e1:
        return False
    return True


async def _default_similarity(client: Any, a: str, b: str) -> float:
    """Best-effort similarity using whatever Graphiti exposes.

    Falls back to 1.0 (no conflict flagged) if no similarity surface is
    available — production wiring should pass an explicit ``similarity_fn``.
    """
    sim = getattr(client, "similarity", None)
    if sim is not None:
        try:
            return float(await sim(a, b))
        except Exception:
            logger.debug("client.similarity raised; treating as 1.0", exc_info=True)
            return 1.0
    # No similarity surface — treat as identical so we don't false-positive.
    return 1.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def detect_decision_conflicts(
    decisions: list[dict],
    client: Any = None,
    similarity_fn: Optional[SimilarityFn] = None,
    threshold: float = DEFAULT_CONFLICT_SIMILARITY_THRESHOLD,
) -> list[dict]:
    """For each pair of Decisions with the same ``module`` and overlapping
    validity windows, check semantic similarity. If similarity < threshold
    flag BOTH records with ``conflict=True``.

    Returns the same list (mutated in place) with the ``conflict`` field set
    on any flagged pair. Records that don't participate in any conflict are
    untouched (no ``conflict`` key added — caller can default to False).

    ``similarity_fn`` takes precedence over ``client.similarity`` so tests
    can inject a deterministic stub.
    """
    if not decisions:
        return decisions

    # Guard the O(n²) similarity loop. Current callers pass at most ~21
    # (recent_decisions limit) so this is a defensive ceiling; if a future
    # caller passes more, skip conflict detection rather than block.
    if len(decisions) > 50:
        logger.warning(
            "detect_decision_conflicts: skipping O(n^2) loop for %d decisions (>50 cap)",
            len(decisions),
        )
        return decisions

    async def _sim(a: str, b: str) -> float:
        if similarity_fn is not None:
            return float(await similarity_fn(a, b))
        return await _default_similarity(client, a, b)

    n = len(decisions)
    for i in range(n):
        d1 = decisions[i]
        for j in range(i + 1, n):
            d2 = decisions[j]

            # Same module gate. Missing module → can't compare; skip.
            m1 = d1.get("module")
            m2 = d2.get("module")
            if m1 is None or m2 is None or m1 != m2:
                continue

            if not _validity_overlap(d1, d2):
                continue

            text1 = d1.get("text", "") or ""
            text2 = d2.get("text", "") or ""
            try:
                similarity = await _sim(text1, text2)
            except Exception:
                logger.debug(
                    "similarity_fn raised for decision pair (%s, %s)",
                    d1.get("id"), d2.get("id"), exc_info=True,
                )
                continue

            if similarity < threshold:
                d1["conflict"] = True
                d2["conflict"] = True

    return decisions
