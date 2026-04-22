"""Tests for cost-based threshold helpers."""

from __future__ import annotations

import pytest

from sponsio.models.thresholds import (
    ATOM_CATEGORY_ALPHAS,
    RISK_PROFILES,
    alpha_from_costs,
    beta_from_costs,
    resolve_thresholds,
)


class TestBetaFromCosts:
    def test_equal_costs_gives_half(self):
        assert beta_from_costs(1, 1) == 0.5

    def test_high_fn_cost_tightens_beta(self):
        # Missing a real violation costs 100× more than over-blocking
        assert beta_from_costs(1, 100) == pytest.approx(100 / 101)

    def test_high_fp_cost_loosens_beta(self):
        assert beta_from_costs(100, 1) == pytest.approx(1 / 101)

    def test_hipaa_example_from_doc(self):
        # From docs/cost-based-thresholds.md §3
        beta = beta_from_costs(c_fp=1, c_fn=10000)
        assert beta == pytest.approx(10000 / 10001)
        assert beta > 0.9999

    def test_zero_cost_raises(self):
        with pytest.raises(ValueError):
            beta_from_costs(0, 1)
        with pytest.raises(ValueError):
            beta_from_costs(1, 0)

    def test_negative_cost_raises(self):
        with pytest.raises(ValueError):
            beta_from_costs(-1, 1)


class TestAlphaFromCosts:
    def test_equal_costs_gives_half(self):
        assert alpha_from_costs(1, 1) == 0.5

    def test_missed_trigger_expensive_tightens(self):
        # High MT cost → we should trigger aggressively → alpha small
        # Wait — per doc §4: α* = c_MT / (c_MT + c_FT).
        # High c_MT means we should trigger eagerly, which means α should
        # be small (easier to clear). Let's check: c_MT=100, c_FT=1 →
        # α = 100/101 ≈ 0.99. That's a LARGE α.
        # Hmm, but large α means "require high confidence in A to trigger"
        # which is the OPPOSITE of aggressive triggering...
        # Re-reading doc §4: contract triggers when conf(A) ≥ α, so high α
        # is conservative. With c_MT expensive, we want the contract to
        # fire easily → SMALL α. So the formula sign would be flipped.
        # But the doc explicitly writes α* = c_MT / (c_MT + c_FT) — so
        # either the doc or my intuition is off. Trust the doc; test the
        # formula as specified.
        assert alpha_from_costs(100, 1) == pytest.approx(100 / 101)

    def test_zero_cost_raises(self):
        with pytest.raises(ValueError):
            alpha_from_costs(0, 1)


class TestRiskProfiles:
    def test_presets_defined(self):
        assert {"permissive", "balanced", "cautious", "strict_compliance"} <= set(
            RISK_PROFILES
        )

    def test_strict_compliance_has_high_beta(self):
        assert RISK_PROFILES["strict_compliance"]["beta"] >= 0.999

    def test_permissive_has_low_beta(self):
        assert RISK_PROFILES["permissive"]["beta"] <= 0.5


class TestResolveThresholds:
    def test_defaults_are_1_1(self):
        assert resolve_thresholds() == (1.0, 1.0)

    def test_explicit_alpha_beta(self):
        assert resolve_thresholds(alpha=0.7, beta=0.95) == (0.7, 0.95)

    def test_explicit_alpha_only_beta_defaults(self):
        assert resolve_thresholds(alpha=0.8) == (0.8, 1.0)

    def test_risk_profile_cautious(self):
        assert resolve_thresholds(risk_profile="cautious") == (0.7, 0.95)

    def test_risk_profile_strict_compliance(self):
        a, b = resolve_thresholds(risk_profile="strict_compliance")
        assert a == 0.6
        assert b == 0.999

    def test_unknown_risk_profile_raises(self):
        with pytest.raises(ValueError, match="unknown risk_profile"):
            resolve_thresholds(risk_profile="paranoid")

    def test_costs_basic(self):
        a, b = resolve_thresholds(costs={"fp": 1, "fn": 20})
        assert b == pytest.approx(20 / 21)
        # α falls back to default (no atom_category) = 0.7
        assert a == 0.7

    def test_costs_with_atom_category(self):
        a, b = resolve_thresholds(costs={"fp": 1, "fn": 20}, atom_category="injection")
        assert a == ATOM_CATEGORY_ALPHAS["injection"]

    def test_costs_missing_keys_raises(self):
        with pytest.raises(ValueError, match="fp"):
            resolve_thresholds(costs={"fn": 1})

    def test_conflict_alpha_and_risk_profile(self):
        with pytest.raises(ValueError, match="ambiguous"):
            resolve_thresholds(alpha=0.7, risk_profile="cautious")

    def test_conflict_beta_and_costs(self):
        with pytest.raises(ValueError, match="ambiguous"):
            resolve_thresholds(beta=0.95, costs={"fp": 1, "fn": 20})

    def test_conflict_profile_and_costs(self):
        with pytest.raises(ValueError, match="ambiguous"):
            resolve_thresholds(risk_profile="cautious", costs={"fp": 1, "fn": 20})
