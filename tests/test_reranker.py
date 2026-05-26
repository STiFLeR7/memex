"""Phase 7 / ARCHITECTURE-v0.3.0 §8 — composite reranker tests.

The composite formula is:

    final = rerank_score
            * exp(-Δt_days / τ)                       # recency, τ=90
            * (conf_floor + (1 - conf_floor) * conf)  # confidence floor 0.5
            * (1 + 0.1 * log(1 + access_count))       # rehearsal boost

with a hard ``expired_at IS NOT NULL`` filter applied BEFORE scoring, and a
final RRF(k=60) merge across modality lists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import exp, log
from typing import Optional

import pytest

from memex.mcp_server.reranker import (
    CONF_FLOOR,
    REHEARSAL_WEIGHT,
    ScoredResult,
    composite_score,
    merge_modalities,
    score_and_rank,
)


@dataclass
class FakeResult:
    """Mimics a Graphiti search result with the fields composite_score reads."""
    uuid: str = "u-1"
    rerank_score: float = 1.0
    valid_at: Optional[datetime] = None
    expired_at: Optional[datetime] = None
    base_confidence: float = 1.0
    last_reinforced_at: Optional[datetime] = None
    validated: bool = True
    access_count: int = 0
    created_at: Optional[datetime] = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# composite_score
# ---------------------------------------------------------------------------


def test_composite_score_drops_expired_results():
    """ARCHITECTURE §8 Step 1 — Graphiti's auto-invalidated edges must never
    leak into agent context. A result with ``expired_at`` set returns None
    from composite_score (so the caller filters it out)."""
    now = _now()
    r = FakeResult(uuid="expired-1", expired_at=now - timedelta(days=2))
    assert composite_score(r, now=now) is None


def test_composite_score_uses_exponential_recency_at_90_day_tau():
    """A 30-day-old result with τ=90 should get recency_factor = exp(-30/90).
    Other factors are held at 1.0 so the composite isolates recency."""
    now = _now()
    r = FakeResult(
        uuid="recent-30d",
        rerank_score=1.0,
        valid_at=now - timedelta(days=30),
        base_confidence=1.0,
        last_reinforced_at=now,   # neutralise confidence decay → conf=1.0
        validated=True,
        access_count=0,            # rehearsal = 1 + 0.1*log(1) = 1.0
    )
    scored = composite_score(r, now=now, tau_days=90)
    assert scored is not None
    expected_recency = exp(-30.0 / 90.0)
    assert scored.recency_factor == pytest.approx(expected_recency, rel=1e-6)
    # All other factors at 1.0 (or 1.0-ish via floor: 0.5 + 0.5*1.0 = 1.0)
    assert scored.confidence_factor == pytest.approx(1.0, rel=1e-6)
    assert scored.rehearsal_boost == pytest.approx(1.0, rel=1e-6)
    assert scored.final_score == pytest.approx(expected_recency, rel=1e-6)


def test_composite_score_uses_confidence_floor_at_0_5():
    """conf_floor + (1 - conf_floor) * conf with conf=0 should give 0.5.
    A node with base_confidence=0 (and no other signals) tests this."""
    now = _now()
    # base_confidence=0 → current_confidence returns 0
    r = FakeResult(
        uuid="no-confidence",
        rerank_score=1.0,
        valid_at=now,              # zero age → recency = 1.0
        base_confidence=0.0,
        last_reinforced_at=now,
        validated=False,
        access_count=0,
    )
    scored = composite_score(r, now=now)
    assert scored is not None
    assert scored.confidence_factor == pytest.approx(CONF_FLOOR, rel=1e-6)
    # Final = 1.0 * 1.0 * 0.5 * 1.0 = 0.5
    assert scored.final_score == pytest.approx(0.5, rel=1e-6)


def test_rehearsal_boost_grows_logarithmically_with_access_count():
    """1 + 0.1*log(1 + access). access=0 → 1.0; access=100 → ~1.46."""
    now = _now()

    def boost_at(access: int) -> float:
        r = FakeResult(
            uuid=f"a-{access}",
            rerank_score=1.0,
            valid_at=now,
            base_confidence=1.0,
            last_reinforced_at=now,
            access_count=access,
        )
        s = composite_score(r, now=now)
        assert s is not None
        return s.rehearsal_boost

    assert boost_at(0) == pytest.approx(1.0, rel=1e-6)

    expected_100 = 1.0 + REHEARSAL_WEIGHT * log(1.0 + 100)
    assert boost_at(100) == pytest.approx(expected_100, rel=1e-6)
    # Numerically ~1.46 (precisely 1.4615 per 1 + 0.1*ln(101))
    assert boost_at(100) == pytest.approx(1.46, abs=1e-2)

    # Monotonic + diminishing returns: doubling access does NOT double the
    # delta. boost(200) - boost(100) < boost(100) - boost(0).
    delta_low = boost_at(100) - boost_at(0)
    delta_high = boost_at(200) - boost_at(100)
    assert delta_high < delta_low


# ---------------------------------------------------------------------------
# merge_modalities (RRF)
# ---------------------------------------------------------------------------


def _make_scored(uuid: str, final: float = 1.0) -> ScoredResult:
    """Build a ScoredResult skipping the composite math — RRF only cares
    about uuid + position in its input list."""
    return ScoredResult(
        uuid=uuid,
        node_or_edge=None,
        modality="edge",
        graphiti_score=1.0,
        recency_factor=1.0,
        confidence_factor=1.0,
        rehearsal_boost=1.0,
        final_score=final,
    )


def test_rrf_merges_across_modalities():
    """Three lists with overlapping IDs. Each list contributes 1/(rank + 60)
    per ID. A result that appears at rank 0 in two lists should have
    rrf_score = 1/60 + 1/60 = 2/60."""
    list_a = [_make_scored("u1"), _make_scored("u2"), _make_scored("u3")]
    list_b = [_make_scored("u1"), _make_scored("u4")]
    list_c = [_make_scored("u2")]

    merged = merge_modalities(list_a, list_b, list_c, k=60)

    by_uuid = {sr.uuid: sr for sr in merged}

    # u1: rank 0 in A + rank 0 in B = 1/60 + 1/60
    assert by_uuid["u1"].rrf_score == pytest.approx(2.0 / 60.0, rel=1e-9)
    # u2: rank 1 in A + rank 0 in C = 1/61 + 1/60
    assert by_uuid["u2"].rrf_score == pytest.approx(1.0 / 61.0 + 1.0 / 60.0, rel=1e-9)
    # u3: rank 2 in A only = 1/62
    assert by_uuid["u3"].rrf_score == pytest.approx(1.0 / 62.0, rel=1e-9)
    # u4: rank 1 in B only = 1/61
    assert by_uuid["u4"].rrf_score == pytest.approx(1.0 / 61.0, rel=1e-9)


def test_rrf_merges_disjoint_lists():
    """No overlap between lists → each result's rrf_score = 1/(rank + 60)."""
    list_a = [_make_scored("a1"), _make_scored("a2")]
    list_b = [_make_scored("b1"), _make_scored("b2")]

    merged = merge_modalities(list_a, list_b, k=60)
    assert len(merged) == 4
    by_uuid = {sr.uuid: sr.rrf_score for sr in merged}

    assert by_uuid["a1"] == pytest.approx(1.0 / 60.0, rel=1e-9)
    assert by_uuid["a2"] == pytest.approx(1.0 / 61.0, rel=1e-9)
    assert by_uuid["b1"] == pytest.approx(1.0 / 60.0, rel=1e-9)
    assert by_uuid["b2"] == pytest.approx(1.0 / 61.0, rel=1e-9)


def test_rrf_orders_by_summed_rrf_score():
    """A result that's high-rank in TWO lists beats one that's top of a
    SINGLE list. uuid 'shared' appears at rank 0 in two lists; uuid 'solo'
    at rank 0 in one list → shared.rrf > solo.rrf, so shared is first."""
    list_a = [_make_scored("solo"), _make_scored("shared")]
    list_b = [_make_scored("shared")]  # shared at rank 0 here

    merged = merge_modalities(list_a, list_b, k=60)
    # shared: 1/61 (rank 1 in A) + 1/60 (rank 0 in B) = .01639... + .01666... = .03306
    # solo: 1/60 = .01666
    assert merged[0].uuid == "shared"
    assert merged[1].uuid == "solo"
    assert merged[0].rrf_score > merged[1].rrf_score


# ---------------------------------------------------------------------------
# score_and_rank — integration of composite + ordering
# ---------------------------------------------------------------------------


def test_score_and_rank_drops_expired_and_sorts_descending():
    now = _now()
    r_fresh = FakeResult(
        uuid="fresh", rerank_score=0.5, valid_at=now,
        base_confidence=1.0, last_reinforced_at=now,
    )
    r_old = FakeResult(
        uuid="old", rerank_score=0.5, valid_at=now - timedelta(days=180),
        base_confidence=1.0, last_reinforced_at=now,
    )
    r_dead = FakeResult(
        uuid="dead", rerank_score=10.0, expired_at=now,
    )

    ranked = score_and_rank([r_dead, r_old, r_fresh], now=now)
    uuids = [sr.uuid for sr in ranked]
    assert "dead" not in uuids
    # Fresh > old after recency decay
    assert uuids[0] == "fresh"
    assert uuids[1] == "old"
