"""StoFormula — the semantic counterpart to DetFormula.

While DetFormula wraps an LTL formula whose leaves are det atoms
(binary pass/fail with :math:`\\alpha=\\beta=1`), StoFormula covers the
sto pipeline — any formula whose leaves include atoms with
``atom_type="sto"``, plus the legacy closure-evaluator path.

Two shapes, exactly one may be set per instance:

* ``formula`` — an LTL ``Formula`` AST whose leaves may be sto Atoms.
  Evaluated via :func:`sponsio.runtime.sto_lifting.eval_sto_confidence`.
  This is the preferred shape for new atom-registered evaluators.
* ``evaluator_fn`` — legacy closure ``(Trace) -> StoResult``. Used by
  the six bundled closure-based categories (pii / length / format /
  tone / relevance / content_prohibition) until they migrate to atom
  form.

Both can appear in ``Contract.enforcement``. StoFormula inside an
``assumption`` field is ignored — assumptions must be det.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from sponsio.formulas.formula import FormulaMixin
from sponsio.models.trace import Trace
from sponsio.runtime.evaluators import StoResult


@dataclass
class StoFormula:
    """A semantic constraint evaluated in the sto pipeline.

    Attributes:
        desc: Human-readable description (the original NL text).
        category: Sto constraint category
            (pii / tone / relevance / format / length /
            content_prohibition / custom or an atom predicate name
            like ``"injection_free"``).
        formula: Optional LTL ``Formula`` whose leaves include sto
            atoms. Mutually exclusive with ``evaluator_fn``.
        evaluator_fn: Legacy scoring closure ``(Trace) -> StoResult``.
            Mutually exclusive with ``formula``.
        threshold: Minimum score to pass (0.0–1.0).
        feedback_template: Optional template for FeedbackGenerator.
        pattern_name: Always ``"sto"`` for discovery parity.
        requires_llm: Whether this evaluator needs an LLM call.
    """

    desc: str
    category: str = "custom"
    formula: Optional[Any] = None  # Formula — Any to avoid fwd-ref issues
    evaluator_fn: Optional[Callable[[Trace], StoResult]] = field(
        default=None, repr=False
    )
    threshold: float = 0.7
    feedback_template: Optional[str] = None
    pattern_name: str = "sto"
    requires_llm: bool = False

    def __post_init__(self) -> None:
        if self.formula is None and self.evaluator_fn is None:
            raise ValueError(
                "StoFormula requires exactly one of `formula` (LTL AST with "
                "sto atoms) or `evaluator_fn` (legacy closure). Both were None."
            )
        if self.formula is not None and self.evaluator_fn is not None:
            raise ValueError(
                "StoFormula: `formula` and `evaluator_fn` are mutually "
                "exclusive. Set one, not both."
            )
        if self.formula is not None and not isinstance(self.formula, FormulaMixin):
            raise TypeError(
                f"StoFormula.formula must be a Formula AST, got "
                f"{type(self.formula).__name__}"
            )


# Backward-compatible alias
StoConstraint = StoFormula
