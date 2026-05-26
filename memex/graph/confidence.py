"""TempValid two-regime computed-confidence helper (Phase 8 / ARCHITECTURE §9).

Confidence is **never stored as a mutating value** in v0.3.0. v0.2.0 stored
``confidence`` on nodes and ran a nightly job to decrement it — that pattern bit
memex twice (no writes to ``r.last_touched`` made the decay a no-op; the writer
for ``r.confidence`` was missing too). v0.3.0 replaces this with a query-time
computation backed by two stored fields (``base_confidence`` +
``last_reinforced_at``) plus the ``validated`` flag.

Derivation of the lambda constants
----------------------------------
The decay constants are anchored to the staleness-threshold cross
(``computed_confidence < 0.3``) from ``base_confidence = 0.6`` (the
watcher-synthesised default per Q3 / Phase 8).

    base * exp(-λ * t) = threshold
    → t = ln(base / threshold) / λ

For base=0.6, threshold=0.3:  t = ln(2) / λ

    t_validated   = 139d  → λ_validated   = ln(2)/139 ≈ 0.00499  (rounded to 0.005)
    t_unvalidated =  30d  → λ_unvalidated = ln(2)/30  ≈ 0.0231

These constants only equal a "half-life" by coincidence — because halving 0.6
yields 0.3. For agent-recorded decisions with higher ``base_confidence``,
time-to-staleness is longer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from math import exp, log
from typing import Any

# Per-day decay constants. Do NOT change without re-deriving the staleness
# crossings from base 0.6.
LAMBDA_VALIDATED = 0.005  # per day; time-to-0.3 from base 0.6 ≈ 139 days
LAMBDA_UNVALIDATED = log(2) / 30  # per day; time-to-0.3 from base 0.6 = exactly 30 days
LAMBDA_UNVALIDATED_OLD = log(2) / 20  # Regime 3 decay rate; 20-day half-life
UNVALIDATED_OLD_THRESHOLD_DAYS = 30
UNVALIDATED_OLD_CAP = 0.5
VALIDATED_FLOOR = 0.7
STALENESS_THRESHOLD = 0.3
COLD_THRESHOLD = 0.05
COLD_MIN_QUIET_DAYS = 90


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _get(node: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from either a dict (Cypher result row) or a Pydantic /
    attribute-style object. Returns ``default`` if missing or None."""
    if node is None:
        return default
    if isinstance(node, dict):
        val = node.get(key, default)
    else:
        val = getattr(node, key, default)
    if val is None:
        return default
    return val


def _as_datetime(value: Any) -> datetime | None:
    """Coerce a value that may be a ``datetime`` or a Neo4j ``DateTime`` /
    string into a Python ``datetime``. Returns ``None`` if not parseable."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    # neo4j.time.DateTime exposes to_native()
    to_native = getattr(value, "to_native", None)
    if callable(to_native):
        try:
            return to_native()
        except Exception:
            pass
    if isinstance(value, str):
        try:
            # ISO format with optional trailing Z
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _ensure_aware(dt: datetime) -> datetime:
    """Make sure the datetime is timezone-aware (UTC if naive)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def current_confidence(node: Any) -> float:
    """Returns the computed (not stored) current confidence of a node.

    Accepts either a dict (from a Cypher result row) or a Pydantic-model /
    attribute-style object.

    Three-regime TempValid confidence:

    Regime 1 — validated=True:
        conf = base_confidence * exp(-LAMBDA_VALIDATED * days)
        floor: VALIDATED_FLOOR (0.7)

    Regime 2 — not validated, age <= UNVALIDATED_OLD_THRESHOLD_DAYS:
        conf = base_confidence * exp(-LAMBDA_UNVALIDATED * days)

    Regime 3 — not validated, age > UNVALIDATED_OLD_THRESHOLD_DAYS:
        conf = base_confidence * exp(-LAMBDA_UNVALIDATED_OLD * days)
        cap: UNVALIDATED_OLD_CAP (0.5)

    Fallbacks (legacy / pre-v0.3.0 nodes):
        - missing ``base_confidence`` → defaults to 1.0 (matches v0.2.0)
        - missing ``last_reinforced_at`` → falls back to ``created_at``
        - missing both anchors → returns ``base_confidence`` unchanged
    """
    base = max(0.0, float(_get(node, "base_confidence", 1.0)))

    last = _as_datetime(_get(node, "last_reinforced_at"))
    if last is None:
        last = _as_datetime(_get(node, "created_at"))
    if last is None:
        # No anchor to decay against — return base unmodified.
        return max(0.0, min(1.0, base))

    last = _ensure_aware(last)
    now = _utc_now()
    days = (now - last).total_seconds() / 86400.0
    if days < 0:
        days = 0.0

    validated = bool(_get(node, "validated", False))
    if validated:
        computed = base * exp(-LAMBDA_VALIDATED * days)
        computed = max(VALIDATED_FLOOR, computed)
    elif days <= UNVALIDATED_OLD_THRESHOLD_DAYS:
        computed = base * exp(-LAMBDA_UNVALIDATED * days)
    else:
        computed = base * exp(-LAMBDA_UNVALIDATED_OLD * days)
        computed = min(UNVALIDATED_OLD_CAP, computed)

    if computed < 0.0:
        return 0.0
    if computed > 1.0:
        return 1.0
    return computed


def is_stale(node: Any) -> bool:
    """``True`` if the node's computed confidence has fallen below the
    staleness threshold (0.3). Used to refresh the cached ``stale`` boolean."""
    return current_confidence(node) < STALENESS_THRESHOLD


def is_cold(node: Any) -> bool:
    """Eligibility for archival. ``True`` iff:

        computed_confidence(node) < 0.05
        AND validated is False
        AND last_reinforced_at > 90 days ago
            (falls back to created_at if last_reinforced_at is missing)

    Validated nodes are NEVER cold — users opted into keeping them.
    """
    if bool(_get(node, "validated", False)):
        return False

    if current_confidence(node) >= COLD_THRESHOLD:
        return False

    last = _as_datetime(_get(node, "last_reinforced_at"))
    if last is None:
        last = _as_datetime(_get(node, "created_at"))
    if last is None:
        # No timestamp anchor; do not archive without evidence.
        return False

    last = _ensure_aware(last)
    days = (_utc_now() - last).total_seconds() / 86400.0
    return days > COLD_MIN_QUIET_DAYS
