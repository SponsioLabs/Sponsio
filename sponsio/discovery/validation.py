"""Shared validation pipeline for discovered constraints.

Validates proposed constraints through 5 steps before they can be
accepted into the pattern store:

1. Syntactic  — can the formula be evaluated?
2. Triviality — is it always true or always false?
3. Consistency — does it contradict existing patterns?
4. Trace replay — how does it perform on historical traces?
5. Human review — mark as PROPOSED (never auto-promoted)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sponsio.discovery._types import ConstraintStatus, ProposedConstraint
from sponsio.formulas.evaluator import evaluate
from sponsio.formulas.formula import (
    And,
    collect_atoms,
)
from sponsio.models.trace import Trace
from sponsio.patterns.library import DetFormula
from sponsio.tracer.grounding import ground


@dataclass
class ValidationResult:
    """Result of a single validation step."""

    passed: bool
    step: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class ValidationPipeline:
    """5-step validation pipeline for proposed constraints.

    Args:
        existing_formulas: Already-verified formulas for consistency check.
        historical_traces: Traces for replay validation.
    """

    def __init__(
        self,
        existing_formulas: Optional[list[DetFormula]] = None,
        historical_traces: Optional[list[Trace]] = None,
    ) -> None:
        self._existing = existing_formulas or []
        self._traces = historical_traces or []

    def validate(self, constraint: ProposedConstraint) -> ProposedConstraint:
        """Run all validation steps. Populates constraint.validation_errors."""
        constraint.validation_errors.clear()

        steps = [
            self._validate_syntactic,
            self._validate_triviality,
            self._validate_consistency,
            self._validate_trace_replay,
            self._mark_for_review,
        ]

        for step_fn in steps:
            result = step_fn(constraint)
            if not result.passed:
                constraint.validation_errors.extend(result.errors)
            # Warnings go into evidence
            if result.warnings:
                constraint.evidence.setdefault("validation_warnings", []).extend(
                    result.warnings
                )

        return constraint

    def validate_batch(
        self, constraints: list[ProposedConstraint]
    ) -> list[ProposedConstraint]:
        """Validate multiple constraints."""
        return [self.validate(c) for c in constraints]

    # -----------------------------------------------------------------
    # Step 1: Syntactic
    # -----------------------------------------------------------------

    def _validate_syntactic(self, constraint: ProposedConstraint) -> ValidationResult:
        """Check that the formula can be evaluated without errors."""
        if constraint.formula is None:
            return ValidationResult(
                passed=False, step="syntactic", errors=["formula is None"]
            )

        raw = (
            constraint.formula.formula
            if isinstance(constraint.formula, DetFormula)
            else constraint.formula
        )

        try:
            evaluate(raw, [{}])
        except Exception as e:
            return ValidationResult(
                passed=False,
                step="syntactic",
                errors=[f"Formula evaluation failed: {e}"],
            )

        return ValidationResult(passed=True, step="syntactic")

    # -----------------------------------------------------------------
    # Step 2: Triviality
    # -----------------------------------------------------------------

    def _validate_triviality(self, constraint: ProposedConstraint) -> ValidationResult:
        """Check that the formula is not always-true or always-false."""
        raw = (
            constraint.formula.formula
            if isinstance(constraint.formula, DetFormula)
            else constraint.formula
        )

        traces = self._generate_synthetic_traces(raw)

        results = []
        for trace in traces:
            try:
                results.append(evaluate(raw, trace))
            except Exception:
                results.append(None)

        valid_results = [r for r in results if r is not None]
        if not valid_results:
            return ValidationResult(passed=True, step="triviality")

        if all(r is True for r in valid_results):
            return ValidationResult(
                passed=False,
                step="triviality",
                errors=["Formula is always true (tautology) on synthetic traces"],
            )

        if all(r is False for r in valid_results):
            return ValidationResult(
                passed=False,
                step="triviality",
                errors=["Formula is always false (contradiction) on synthetic traces"],
            )

        return ValidationResult(passed=True, step="triviality")

    def _generate_synthetic_traces(self, formula) -> list[list[dict]]:
        """Generate synthetic grounded traces for triviality testing."""
        atoms = collect_atoms(formula)
        tools = [a.args[0] for a in atoms if a.predicate == "called" and a.args]

        traces: list[list[dict]] = []
        # Empty trace
        traces.append([{}])

        # Single tool traces
        for t in tools:
            traces.append([{f"called({t})": True, f"count({t})": 1}])

        # All tools at once
        if tools:
            step = {}
            for i, t in enumerate(tools):
                step[f"called({t})"] = True
                step[f"count({t})"] = i + 1
            traces.append([step])

        # Tools in sequence (each in its own step)
        if len(tools) >= 2:
            seq = []
            for i, t in enumerate(tools):
                s = {f"called({t})": True, f"count({t})": i + 1}
                # Add precedes for all prior tools
                for prev in tools[:i]:
                    s[f"precedes({prev}, {t})"] = True
                seq.append(s)
            traces.append(seq)

            # Reversed order
            rev = []
            rtool = list(reversed(tools))
            for i, t in enumerate(rtool):
                s = {f"called({t})": True, f"count({t})": i + 1}
                for prev in rtool[:i]:
                    s[f"precedes({prev}, {t})"] = True
                rev.append(s)
            traces.append(rev)

        return traces

    # -----------------------------------------------------------------
    # Step 3: Consistency
    # -----------------------------------------------------------------

    def _validate_consistency(self, constraint: ProposedConstraint) -> ValidationResult:
        """Check that the formula does not contradict existing patterns."""
        if not self._existing:
            return ValidationResult(passed=True, step="consistency")

        raw_new = (
            constraint.formula.formula
            if isinstance(constraint.formula, DetFormula)
            else constraint.formula
        )

        traces = self._generate_synthetic_traces(raw_new)
        warnings = []

        for existing in self._existing:
            raw_existing = (
                existing.formula if isinstance(existing, DetFormula) else existing
            )

            # Check if conjunction is always false (contradiction)
            conjunction = And(raw_new, raw_existing)
            conj_results = []
            for trace in traces:
                try:
                    conj_results.append(evaluate(conjunction, trace))
                except Exception:
                    pass

            valid = [r for r in conj_results if r is not None]
            if valid and all(r is False for r in valid):
                desc = (
                    existing.desc if isinstance(existing, DetFormula) else str(existing)
                )
                return ValidationResult(
                    passed=False,
                    step="consistency",
                    errors=[f"Contradicts existing pattern: {desc}"],
                )

        return ValidationResult(passed=True, step="consistency", warnings=warnings)

    # -----------------------------------------------------------------
    # Step 4: Trace replay
    # -----------------------------------------------------------------

    def _validate_trace_replay(
        self, constraint: ProposedConstraint
    ) -> ValidationResult:
        """Evaluate formula against historical traces."""
        if not self._traces:
            return ValidationResult(passed=True, step="trace_replay")

        raw = (
            constraint.formula.formula
            if isinstance(constraint.formula, DetFormula)
            else constraint.formula
        )

        pass_count = 0
        fail_count = 0

        for trace in self._traces:
            try:
                grounded = ground(trace)
                result = evaluate(raw, grounded)
                if result:
                    pass_count += 1
                else:
                    fail_count += 1
            except Exception:
                pass

        total = pass_count + fail_count
        if total == 0:
            return ValidationResult(passed=True, step="trace_replay")

        pass_rate = pass_count / total
        constraint.evidence["trace_replay"] = {
            "pass_count": pass_count,
            "fail_count": fail_count,
            "total": total,
            "pass_rate": round(pass_rate, 3),
        }

        warnings = []
        if pass_rate < 0.5:
            warnings.append(
                f"Constraint would have been violated in {fail_count}/{total} "
                f"({round((1 - pass_rate) * 100)}%) of historical traces"
            )

        return ValidationResult(passed=True, step="trace_replay", warnings=warnings)

    # -----------------------------------------------------------------
    # Step 5: Human review marker
    # -----------------------------------------------------------------

    def _mark_for_review(self, constraint: ProposedConstraint) -> ValidationResult:
        """Ensure auto-extracted constraints are marked as PROPOSED."""
        if constraint.source.value == "auto_extracted":
            constraint.status = ConstraintStatus.PROPOSED
        return ValidationResult(passed=True, step="human_review")
