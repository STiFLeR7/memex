"""Phase 7 / ARCHITECTURE-v0.3.0 §8 — Decision conflict detection.

For each pair of Decisions with the same ``module`` and overlapping validity
windows, run a semantic similarity check. If similarity < threshold (default
0.4) flag BOTH records with ``conflict=True`` so the agent can resolve via
``invalidate_edge()`` or ``record_decision(supersedes=...)``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from memex.mcp_server.conflict import (
    DEFAULT_CONFLICT_SIMILARITY_THRESHOLD,
    detect_decision_conflicts,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_decision(
    *,
    id: str,
    module: str,
    text: str,
    valid_from: datetime,
    valid_until: datetime | None = None,
) -> dict:
    return {
        "id": id,
        "module": module,
        "text": text,
        "valid_from": valid_from,
        "valid_until": valid_until,
    }


def _const_similarity(value: float):
    """Return an async similarity stub that always returns ``value``."""
    async def _sim(a: str, b: str) -> float:
        return value
    return _sim


# ---------------------------------------------------------------------------
# Positive case — low similarity + same module + overlap → both flagged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_conflicts_flags_low_similarity_overlapping_decisions():
    now = _now()
    d1 = _make_decision(
        id="d1", module="auth/service.py",
        text="Switch auth to EdDSA",
        valid_from=now - timedelta(days=10),
        valid_until=None,                  # still valid
    )
    d2 = _make_decision(
        id="d2", module="auth/service.py",
        text="Roll back to RSA for compatibility",
        valid_from=now - timedelta(days=5),
        valid_until=None,                  # still valid → overlaps
    )

    result = await detect_decision_conflicts(
        [d1, d2], similarity_fn=_const_similarity(0.2),
    )
    assert result[0].get("conflict") is True
    assert result[1].get("conflict") is True


# ---------------------------------------------------------------------------
# Negative cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_conflicts_does_not_flag_high_similarity():
    """High similarity (>= threshold) means the decisions corroborate
    rather than conflict — no flag."""
    now = _now()
    d1 = _make_decision(
        id="d1", module="auth/service.py",
        text="Use EdDSA for signing",
        valid_from=now - timedelta(days=10),
    )
    d2 = _make_decision(
        id="d2", module="auth/service.py",
        text="EdDSA chosen for auth keys",
        valid_from=now - timedelta(days=5),
    )

    result = await detect_decision_conflicts(
        [d1, d2], similarity_fn=_const_similarity(0.9),
    )
    assert "conflict" not in result[0]
    assert "conflict" not in result[1]


@pytest.mark.asyncio
async def test_detect_conflicts_does_not_flag_non_overlapping_validity():
    """Sequential validity windows (d1 ends before d2 starts) → no overlap,
    no conflict even with low similarity."""
    now = _now()
    d1 = _make_decision(
        id="d1", module="auth/service.py",
        text="Use HS256",
        valid_from=now - timedelta(days=100),
        valid_until=now - timedelta(days=50),   # closed BEFORE d2
    )
    d2 = _make_decision(
        id="d2", module="auth/service.py",
        text="Use EdDSA",
        valid_from=now - timedelta(days=30),
        valid_until=None,
    )

    result = await detect_decision_conflicts(
        [d1, d2], similarity_fn=_const_similarity(0.1),
    )
    assert "conflict" not in result[0]
    assert "conflict" not in result[1]


@pytest.mark.asyncio
async def test_detect_conflicts_does_not_flag_different_modules():
    """Decisions in different modules cannot conflict semantically — the
    same-module gate must short-circuit before similarity is even called."""
    now = _now()
    d1 = _make_decision(
        id="d1", module="auth/service.py",
        text="Use EdDSA",
        valid_from=now - timedelta(days=10),
    )
    d2 = _make_decision(
        id="d2", module="billing/charge.py",
        text="Use RSA",
        valid_from=now - timedelta(days=5),
    )

    sim_calls: list[tuple[str, str]] = []

    async def _spy_sim(a: str, b: str) -> float:
        sim_calls.append((a, b))
        return 0.0  # would flag if reached

    result = await detect_decision_conflicts([d1, d2], similarity_fn=_spy_sim)
    assert "conflict" not in result[0]
    assert "conflict" not in result[1]
    # Same-module gate must short-circuit before similarity is queried.
    assert sim_calls == []


# ---------------------------------------------------------------------------
# Threshold wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_conflicts_threshold_default_matches_architecture():
    """Sanity check that the default threshold matches §8 (0.4)."""
    assert DEFAULT_CONFLICT_SIMILARITY_THRESHOLD == 0.4


@pytest.mark.asyncio
async def test_detect_conflicts_explicit_threshold_overrides_default():
    now = _now()
    d1 = _make_decision(
        id="d1", module="m", text="A",
        valid_from=now - timedelta(days=10),
    )
    d2 = _make_decision(
        id="d2", module="m", text="B",
        valid_from=now - timedelta(days=5),
    )
    # Similarity 0.5 — above the 0.4 default (no flag), below a 0.6
    # custom threshold (would flag).
    res_default = await detect_decision_conflicts(
        [dict(d1), dict(d2)], similarity_fn=_const_similarity(0.5),
    )
    assert "conflict" not in res_default[0]

    res_strict = await detect_decision_conflicts(
        [dict(d1), dict(d2)], similarity_fn=_const_similarity(0.5),
        threshold=0.6,
    )
    assert res_strict[0]["conflict"] is True
    assert res_strict[1]["conflict"] is True
