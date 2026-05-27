"""Tests for the TempValid two-regime computed-confidence helper.

These exercise the math, the validated/unvalidated regime split, fallbacks for
legacy nodes, and the ``is_cold`` archival eligibility rule.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import log

from unittest.mock import patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

@pytest.fixture
def frozen_now():
    now_val = datetime.now(timezone.utc)
    with patch("memex.graph.confidence._utc_now", return_value=now_val):
        yield now_val

from memex.graph.confidence import (
    LAMBDA_UNVALIDATED,
    LAMBDA_VALIDATED,
    STALENESS_THRESHOLD,
    current_confidence,
    is_cold,
    is_stale,
)


# ---------------------------------------------------------------------------
# Constants — anchored to ARCHITECTURE §9 / Phase 8 advisor-corrected math.
# ---------------------------------------------------------------------------


def test_lambda_unvalidated_is_ln2_over_30_exactly():
    """λ_unvalidated must be ln(2)/30 (NOT the pre-advisor 0.04)."""
    assert LAMBDA_UNVALIDATED == log(2) / 30


def test_lambda_validated_is_0_005():
    """λ_validated is 0.005 (≈ ln(2)/139)."""
    assert LAMBDA_VALIDATED == 0.005


def test_staleness_threshold_is_0_3():
    assert STALENESS_THRESHOLD == 0.3


# ---------------------------------------------------------------------------
# Two-regime decay math.
# ---------------------------------------------------------------------------


def _make_node(**kwargs):
    """Build a dict-shaped node with the given overrides; everything else
    defaults to the watcher-synthesised baseline (base=0.6, unvalidated)."""
    base = {
        "base_confidence": 0.6,
        "validated": False,
        "last_reinforced_at": datetime.now(timezone.utc),
    }
    base.update(kwargs)
    return base


def test_unvalidated_decision_crosses_0_3_at_day_30(frozen_now):
    """The headline guarantee: unvalidated base=0.6 → stale at exactly d=30."""
    anchor = frozen_now - timedelta(days=30)
    node = _make_node(last_reinforced_at=anchor)
    computed = current_confidence(node)
    # ln(2)/30 * 30 = ln(2) → 0.6 * exp(-ln2) = 0.3 exactly
    assert computed == pytest.approx(0.3, abs=1e-3)


def test_validated_decision_never_crosses_validated_floor():
    """Validated decisions never decay below the 0.7 floor."""
    anchor = datetime.now(timezone.utc) - timedelta(days=1000)
    node = _make_node(validated=True, last_reinforced_at=anchor)
    computed = current_confidence(node)
    assert computed == pytest.approx(0.7, abs=1e-6)


def test_validated_decision_is_not_stale_at_day_30(frozen_now):
    """The whole point of validation: 30 days in, validated decisions are fine."""
    anchor = frozen_now - timedelta(days=30)
    node = _make_node(validated=True, last_reinforced_at=anchor)
    assert current_confidence(node) > STALENESS_THRESHOLD
    assert not is_stale(node)


def test_unvalidated_decision_at_day_0_returns_base():
    """No decay at day zero."""
    node = _make_node()
    assert current_confidence(node) == pytest.approx(0.6, abs=1e-6)


def test_computed_uses_validated_lambda_when_validated_true():
    anchor = datetime.now(timezone.utc) - timedelta(days=60)
    unvalidated = _make_node(last_reinforced_at=anchor, validated=False)
    validated = _make_node(last_reinforced_at=anchor, validated=True)
    # at d=60, validated is much higher than unvalidated
    assert current_confidence(validated) > current_confidence(unvalidated)


def test_computed_clamped_to_zero_minimum():
    anchor = datetime.now(timezone.utc) - timedelta(days=10_000)
    node = _make_node(last_reinforced_at=anchor)
    assert current_confidence(node) >= 0.0


def test_computed_clamped_to_one_maximum():
    """If base accidentally > 1.0 (shouldn't happen but defensive)."""
    node = _make_node(base_confidence=2.0)
    assert current_confidence(node) <= 1.0


# ---------------------------------------------------------------------------
# Fallbacks for legacy / missing-field nodes.
# ---------------------------------------------------------------------------


def test_missing_base_confidence_defaults_to_one():
    """Legacy v0.2.0 nodes had no base_confidence → default 1.0 keeps them
    surface-able instead of nuking them."""
    node = {"last_reinforced_at": datetime.now(timezone.utc)}
    assert current_confidence(node) == pytest.approx(1.0, abs=1e-6)


def test_missing_last_reinforced_falls_back_to_created_at(frozen_now):
    created = frozen_now - timedelta(days=30)
    node = {"base_confidence": 0.6, "created_at": created, "validated": False}
    # Should behave the same as if last_reinforced_at were set to created_at.
    assert current_confidence(node) == pytest.approx(0.3, abs=1e-3)


def test_missing_both_anchors_returns_base_unchanged():
    """Without any time anchor, we cannot decay — return base."""
    node = {"base_confidence": 0.7}
    assert current_confidence(node) == pytest.approx(0.7, abs=1e-6)


def test_accepts_pydantic_like_attribute_object(frozen_now):
    """The helper must work on both dicts and attribute-style objects."""
    class Stub:
        base_confidence = 0.6
        validated = False
        last_reinforced_at = frozen_now - timedelta(days=30)
        created_at = None

    assert current_confidence(Stub()) == pytest.approx(0.3, abs=1e-3)


def test_handles_naive_datetime_by_assuming_utc(frozen_now):
    """Some Cypher results come back naive — don't crash."""
    anchor_naive = (frozen_now - timedelta(days=30)).replace(tzinfo=None)
    node = {
        "base_confidence": 0.6,
        "validated": False,
        "last_reinforced_at": anchor_naive,
    }
    # Should still compute (treating naive as UTC), not raise.
    assert current_confidence(node) == pytest.approx(0.3, abs=1e-2)


# ---------------------------------------------------------------------------
# is_stale / is_cold.
# ---------------------------------------------------------------------------


def test_is_stale_true_when_below_threshold():
    anchor = datetime.now(timezone.utc) - timedelta(days=40)
    node = _make_node(last_reinforced_at=anchor)
    # ln(2)/30 * 40 → 0.6 * exp(-1.333) ≈ 0.158 < 0.3
    assert is_stale(node)


def test_is_stale_false_when_above_threshold():
    anchor = datetime.now(timezone.utc) - timedelta(days=15)
    node = _make_node(last_reinforced_at=anchor)
    assert not is_stale(node)


def test_is_cold_requires_low_conf_and_unvalidated_and_quiet():
    """All three conditions must hold for is_cold to fire."""
    anchor = datetime.now(timezone.utc) - timedelta(days=200)
    cold = _make_node(last_reinforced_at=anchor, base_confidence=0.6, validated=False)
    assert is_cold(cold)


def test_is_cold_false_when_validated():
    """Validated nodes are never cold — users opted into keeping them."""
    anchor = datetime.now(timezone.utc) - timedelta(days=10_000)
    node = _make_node(last_reinforced_at=anchor, validated=True)
    assert not is_cold(node)


def test_is_cold_false_within_90_day_quiet_window():
    """Even if computed_confidence is low, we must wait 90+ days quiet."""
    anchor = datetime.now(timezone.utc) - timedelta(days=60)
    node = _make_node(last_reinforced_at=anchor, base_confidence=0.6)
    # at d=60 unvalidated: 0.6 * exp(-ln(2)/30 * 60) = 0.6 * 0.25 = 0.15 (not <0.05)
    # → not cold even apart from the 90d gate
    assert not is_cold(node)


def test_is_cold_false_without_any_timestamp():
    """No timestamp anchor → cannot prove > 90 days quiet → not cold."""
    node = {"base_confidence": 0.6, "validated": False}
    assert not is_cold(node)


def test_is_cold_respects_90_day_quiet_window_boundary():
    """Under Regime 3 (half-life 20 days), unvalidated nodes older than 90 days
    are always < 0.05, so quiet window (90 days) is the main gating factor.
    """
    # At d=89, computed is < 0.05 but age is <= 90 days -> not cold.
    anchor_89 = datetime.now(timezone.utc) - timedelta(days=89)
    node_89 = _make_node(last_reinforced_at=anchor_89, base_confidence=0.6)
    assert not is_cold(node_89)

    # At d=91, computed is < 0.05 and age > 90 days -> cold.
    anchor_91 = datetime.now(timezone.utc) - timedelta(days=91)
    node_91 = _make_node(last_reinforced_at=anchor_91, base_confidence=0.6)
    assert is_cold(node_91)


@given(
    base_confidence=st.floats(min_value=0.0, max_value=2.0),
    days_since_reinforced=st.floats(min_value=0.0, max_value=3650.0),
    validated=st.booleans(),
)
@settings(max_examples=200)
def test_confidence_invariants(base_confidence, days_since_reinforced, validated):
    anchor = datetime.now(timezone.utc) - timedelta(days=days_since_reinforced)
    node = {
        "base_confidence": base_confidence,
        "validated": validated,
        "last_reinforced_at": anchor,
    }
    conf = current_confidence(node)
    
    # Invariant 1: confidence always in [0.0, 1.0]
    assert 0.0 <= conf <= 1.0

    # Invariant 2: validated floor 0.7 never breached for validated=True nodes
    if validated:
        assert conf >= 0.7

    # Invariant 3: unvalidated old nodes cap at 0.5 when days > 30
    if not validated and days_since_reinforced > 30.0:
        assert conf <= 0.5


@given(
    base_confidence=st.floats(min_value=0.0, max_value=1.0),
    days1=st.floats(min_value=0.0, max_value=3000.0),
    days2=st.floats(min_value=0.0, max_value=3000.0),
    validated=st.booleans(),
)
@settings(max_examples=200)
def test_confidence_monotonicity(base_confidence, days1, days2, validated):
    # Sort them to know which is earlier/later
    d_early, d_late = min(days1, days2), max(days1, days2)
    
    anchor_early = datetime.now(timezone.utc) - timedelta(days=d_early)
    anchor_late = datetime.now(timezone.utc) - timedelta(days=d_late)
    
    node_early = {
        "base_confidence": base_confidence,
        "validated": validated,
        "last_reinforced_at": anchor_early,
    }
    node_late = {
        "base_confidence": base_confidence,
        "validated": validated,
        "last_reinforced_at": anchor_late,
    }
    
    conf_early = current_confidence(node_early)
    conf_late = current_confidence(node_late)
    
    # Early reinforced should have >= confidence than late reinforced
    assert conf_early >= conf_late - 1e-9
