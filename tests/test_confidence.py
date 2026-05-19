"""Tests for the TempValid two-regime computed-confidence helper.

These exercise the math, the validated/unvalidated regime split, fallbacks for
legacy nodes, and the ``is_cold`` archival eligibility rule.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import exp, log

import pytest

from memex.graph import confidence as conf_mod
from memex.graph.confidence import (
    COLD_THRESHOLD,
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


def test_unvalidated_decision_crosses_0_3_at_day_30():
    """The headline guarantee: unvalidated base=0.6 → stale at exactly d=30."""
    anchor = datetime.now(timezone.utc) - timedelta(days=30)
    node = _make_node(last_reinforced_at=anchor)
    computed = current_confidence(node)
    # ln(2)/30 * 30 = ln(2) → 0.6 * exp(-ln2) = 0.3 exactly
    assert computed == pytest.approx(0.3, abs=1e-3)


def test_validated_decision_crosses_0_3_at_day_139():
    """Validated regime takes 139 days to cross the staleness threshold."""
    anchor = datetime.now(timezone.utc) - timedelta(days=139)
    node = _make_node(validated=True, last_reinforced_at=anchor)
    computed = current_confidence(node)
    # 0.6 * exp(-0.005 * 139) ≈ 0.6 * exp(-0.695) ≈ 0.2997
    assert computed == pytest.approx(0.3, abs=0.01)


def test_validated_decision_is_not_stale_at_day_30():
    """The whole point of validation: 30 days in, validated decisions are fine."""
    anchor = datetime.now(timezone.utc) - timedelta(days=30)
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


def test_missing_last_reinforced_falls_back_to_created_at():
    created = datetime.now(timezone.utc) - timedelta(days=30)
    node = {"base_confidence": 0.6, "created_at": created, "validated": False}
    # Should behave the same as if last_reinforced_at were set to created_at.
    assert current_confidence(node) == pytest.approx(0.3, abs=1e-3)


def test_missing_both_anchors_returns_base_unchanged():
    """Without any time anchor, we cannot decay — return base."""
    node = {"base_confidence": 0.7}
    assert current_confidence(node) == pytest.approx(0.7, abs=1e-6)


def test_accepts_pydantic_like_attribute_object():
    """The helper must work on both dicts and attribute-style objects."""
    class Stub:
        base_confidence = 0.6
        validated = False
        last_reinforced_at = datetime.now(timezone.utc) - timedelta(days=30)
        created_at = None

    assert current_confidence(Stub()) == pytest.approx(0.3, abs=1e-3)


def test_handles_naive_datetime_by_assuming_utc():
    """Some Cypher results come back naive — don't crash."""
    anchor_naive = (datetime.now(timezone.utc) - timedelta(days=30)).replace(tzinfo=None)
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


def test_is_cold_respects_005_threshold():
    """Boundary: just above 0.05 → not cold; below → cold (when quiet)."""
    # Pick a quiet-enough anchor (>90d) and tune base so computed is just above 0.05.
    anchor = datetime.now(timezone.utc) - timedelta(days=120)
    # 0.6 * exp(-ln(2)/30 * 120) = 0.6 * 1/16 = 0.0375 < 0.05 → cold
    cold = _make_node(last_reinforced_at=anchor, base_confidence=0.6)
    assert is_cold(cold)
    # At d=120 we'd need base s.t. base * 1/16 = 0.10 → base ≈ 1.6 (clamped 1.0)
    # → computed=1.0/16=0.0625 > COLD_THRESHOLD (0.05) → NOT cold.
    not_cold = _make_node(last_reinforced_at=anchor, base_confidence=1.0)
    assert not is_cold(not_cold)
