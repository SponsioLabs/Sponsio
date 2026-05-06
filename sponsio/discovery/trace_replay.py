"""Minimal trace replay for OSS `sponsio validate --traces`.

Given a compiled formula and a list of traces, returns pass / fail
counts and pass_rate.  Counts only — no per-fail attribution, no
stratification by decision, no repair suggestions.  Those richer
features live in the proprietary ``sponsio-pro`` package.

Used by ``sponsio validate --traces`` to give OSS users a basic
"would this contract have hit historical traffic" report before they
flip to enforce mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sponsio.formulas.evaluator import evaluate
from sponsio.models.trace import Trace
from sponsio.tracer.grounding import ground


@dataclass
class TraceReplayResult:
    """Counts for a single contract evaluated against a trace set.

    Attributes:
        pass_count: Number of traces where the formula evaluated to True.
        fail_count: Number of traces where it evaluated to False.
        error_count: Traces that raised during evaluation (e.g. grounding
            referenced an atom not present in the trace).  Excluded
            from pass_rate so a partial coverage gap doesn't skew the
            number, but surfaced separately so users notice.
        errors: Up to 5 sample error messages — for debugging without
            flooding the report.
    """

    pass_count: int = 0
    fail_count: int = 0
    error_count: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        """Traces that produced a definite True/False (excludes errors)."""
        return self.pass_count + self.fail_count

    @property
    def pass_rate(self) -> float | None:
        """Fraction of conclusive traces where the contract held; None
        when no trace produced a result (all errored / list empty)."""
        if self.total == 0:
            return None
        return self.pass_count / self.total


def replay_formula(formula, traces: list[Trace]) -> TraceReplayResult:
    """Evaluate ``formula`` against each trace in ``traces``.

    Args:
        formula: The formula AST (or any object whose ``.formula`` is
            an AST — this matches both raw AST nodes and ``DetFormula``
            wrappers from the pattern library).
        traces: Pre-loaded ``Trace`` objects.  Use
            :func:`sponsio.discovery.loaders.load_traces` to read from
            disk.

    Returns:
        A ``TraceReplayResult`` with per-trace counts.

    Notes:
        Errors are caught individually so one bad trace doesn't abort
        the replay — the return value's ``error_count`` reports them.
    """
    raw = getattr(formula, "formula", formula)
    out = TraceReplayResult()

    for trace in traces:
        try:
            grounded = ground(trace)
            verdict = evaluate(raw, grounded)
        except Exception as e:
            out.error_count += 1
            if len(out.errors) < 5:
                out.errors.append(f"{type(e).__name__}: {e}")
            continue

        if verdict:
            out.pass_count += 1
        else:
            out.fail_count += 1

    return out
