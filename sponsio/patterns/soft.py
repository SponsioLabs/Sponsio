"""StoFormula — the semantic counterpart to DetFormula.

While DetFormula wraps an LTL formula for the det pipeline
(binary pass/fail), StoFormula wraps a scoring function for
the sto pipeline (0–1 score, threshold, feedback).

Both can appear in ``Contract.enforcement``. StoFormula inside an
``assumption`` field is ignored — assumptions must be det.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from sponsio.models.trace import Trace
from sponsio.runtime.evaluators import StoResult


@dataclass
class StoFormula:
    """A semantic constraint evaluated by a scoring function.

    Attributes:
        desc: Human-readable description (the original NL text).
        category: Sto constraint category (pii, tone, relevance, format, length, custom).
        evaluator_fn: Scoring function ``(Trace) -> StoResult``.
        threshold: Minimum score to pass (0.0–1.0).
        feedback_template: Optional template for FeedbackGenerator.
        pattern_name: Always ``"sto"`` for discovery parity.
        requires_llm: Whether this evaluator needs an LLM call.
    """

    desc: str
    category: str = "custom"
    evaluator_fn: Callable[[Trace], StoResult] = field(default=None, repr=False)  # type: ignore[assignment]
    threshold: float = 0.7
    feedback_template: Optional[str] = None
    pattern_name: str = "sto"
    requires_llm: bool = False


# Backward-compatible alias
StoConstraint = StoFormula
