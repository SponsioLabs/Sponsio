"""Tests for sponsio/discovery/_types.py."""

from sponsio.discovery._types import (
    ConstraintStatus,
    DiscoverySource,
    ProposedConstraint,
)
from sponsio.patterns.library import must_precede


def test_discovery_source_values():
    assert DiscoverySource.BUILTIN.value == "builtin"
    assert DiscoverySource.USER_DEFINED.value == "user_defined"
    assert DiscoverySource.AUTO_EXTRACTED.value == "auto_extracted"


def test_constraint_status_values():
    assert ConstraintStatus.PROPOSED.value == "proposed"
    assert ConstraintStatus.VERIFIED.value == "verified"
    assert ConstraintStatus.REJECTED.value == "rejected"


def test_proposed_constraint_ok_when_no_errors():
    formula = must_precede("A", "B")
    pc = ProposedConstraint(formula=formula)
    assert pc.ok is True


def test_proposed_constraint_not_ok_with_errors():
    formula = must_precede("A", "B")
    pc = ProposedConstraint(formula=formula, validation_errors=["some error"])
    assert pc.ok is False


def test_proposed_constraint_defaults():
    formula = must_precede("A", "B")
    pc = ProposedConstraint(formula=formula)
    assert pc.source == DiscoverySource.AUTO_EXTRACTED
    assert pc.status == ConstraintStatus.PROPOSED
    assert pc.confidence == 1.0
    assert pc.extractor == ""
    assert pc.evidence == {}
