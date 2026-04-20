"""Shared types for the contract discovery system."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from sponsio.patterns.library import DetFormula
from sponsio.patterns.sto import StoFormula


class DiscoverySource(str, Enum):
    """Where a pattern came from."""

    BUILTIN = "builtin"
    USER_DEFINED = "user_defined"
    AUTO_EXTRACTED = "auto_extracted"


class ConstraintStatus(str, Enum):
    """Lifecycle status of a discovered constraint."""

    PROPOSED = "proposed"
    VERIFIED = "verified"
    REJECTED = "rejected"


@dataclass
class ProposedConstraint:
    """A constraint candidate produced by any discovery extractor.

    This is the universal output type shared by all three extraction
    phases (document, trace mining, code analysis).

    Attributes:
        formula: The compiled LTL formula with description.
        source: Where the constraint originated.
        extractor: Which extractor produced it.
        confidence: Confidence score from 0.0 to 1.0.
        status: Current lifecycle status.
        provenance: Specific origin (doc section, trace file, code path).
        nl_description: Human-readable natural language form.
        evidence: Extractor-specific supporting data.
        validation_errors: Errors found during validation.
    """

    formula: DetFormula | None = None
    assumption: DetFormula | None = None
    sto: StoFormula | None = None
    source: DiscoverySource = DiscoverySource.AUTO_EXTRACTED
    extractor: str = ""
    confidence: float = 1.0
    status: ConstraintStatus = ConstraintStatus.PROPOSED
    provenance: str = ""
    nl_description: str = ""
    evidence: dict = field(default_factory=dict)
    validation_errors: list[str] = field(default_factory=list)

    @property
    def is_sto(self) -> bool:
        """True if this is a sto (stochastic) constraint."""
        return self.sto is not None

    @property
    def ok(self) -> bool:
        """True if the constraint passed validation with no errors."""
        return len(self.validation_errors) == 0 and (
            self.formula is not None or self.sto is not None
        )
