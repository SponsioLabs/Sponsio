"""First-Order Logic predicate AST and evaluator.

.. deprecated:: 0.2
    The FOL module is deprecated. Use the unified Atom system in
    ``sponsio.formulas.formula`` + ``sponsio.tracer.grounding`` instead.
    FOL predicates have been replaced by grounding-level atoms:
    - ``arg_blacklist`` -> ``arg_has(tool, pattern)``
    - ``scope_limit`` -> ``arg_paths_within(tool, *prefixes)``
    - ``data_intact`` -> ``arg_has`` + ``arg_paths_within`` composition
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from typing import Any, Union

from sponsio.models.trace import Event

warnings.warn(
    "sponsio.formulas.fol is deprecated. "
    "Use the unified Atom system (sponsio.formulas.formula + sponsio.tracer.grounding) instead.",
    DeprecationWarning,
    stacklevel=2,
)


# ---------------------------------------------------------------------------
# Value references
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Field:
    """Reference to an event attribute.

    Dot-separated path: "tool", "agent", "args.command", "content".
    """

    path: str


@dataclass(frozen=True)
class Literal:
    """A constant value."""

    value: str | int | float | bool


# ---------------------------------------------------------------------------
# Comparison predicates (leaf nodes)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Equals:
    """field == value"""

    field: Field
    value: Literal


@dataclass(frozen=True)
class Matches:
    """re.search(pattern, field_value) is not None"""

    field: Field
    pattern: str


@dataclass(frozen=True)
class HasPrefix:
    """any(field_value.startswith(p) for p in prefixes)"""

    field: Field
    prefixes: tuple[str, ...]


@dataclass(frozen=True)
class InSet:
    """field_value in values"""

    field: Field
    values: frozenset[str]


@dataclass(frozen=True)
class GreaterThan:
    """field > threshold"""

    field: Field
    threshold: int | float


@dataclass(frozen=True)
class LessThanEq:
    """field <= threshold"""

    field: Field
    threshold: int | float


# ---------------------------------------------------------------------------
# Boolean connectives
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PNot:
    """Negation."""

    child: "Predicate"


@dataclass(frozen=True)
class PAnd:
    """Conjunction."""

    left: "Predicate"
    right: "Predicate"


@dataclass(frozen=True)
class POr:
    """Disjunction."""

    left: "Predicate"
    right: "Predicate"


@dataclass(frozen=True)
class PImplies:
    """Implication: guard → body.

    If the guard is false, the predicate is vacuously true.
    """

    guard: "Predicate"
    body: "Predicate"


# ---------------------------------------------------------------------------
# Quantifier over sub-structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForAllPaths:
    """Universal quantification over file paths extracted from a field.

    Extracts absolute paths from the field value, then checks that
    the path_predicate holds for each one. The path_predicate receives
    each path via Field("_path").
    """

    field: Field
    path_predicate: "Predicate"


# ---------------------------------------------------------------------------
# Union type
# ---------------------------------------------------------------------------

Predicate = Union[
    Equals,
    Matches,
    HasPrefix,
    InSet,
    GreaterThan,
    LessThanEq,
    PNot,
    PAnd,
    POr,
    PImplies,
    ForAllPaths,
]


# ---------------------------------------------------------------------------
# Field resolution
# ---------------------------------------------------------------------------


def resolve_field(
    field: Field, event: Event, extra: dict[str, Any] | None = None
) -> Any:
    """Resolve a Field path against an Event.

    Supports:
        "tool"          → event.tool
        "agent"         → event.agent
        "event_type"    → event.event_type
        "content"       → event.content
        "args.command"  → event.args["command"]
        "args.X"        → event.args["X"]
        "_path"         → extra["_path"] (for ForAllPaths)

    Returns None if the path cannot be resolved.
    """
    path = field.path

    # Special: quantifier-bound variable
    if path.startswith("_") and extra and path in extra:
        return extra[path]

    # Top-level Event attributes
    if path == "tool":
        return event.tool
    if path == "agent":
        return event.agent
    if path == "event_type":
        return event.event_type
    if path == "content":
        return event.content

    # Nested: args.X
    if path.startswith("args."):
        key = path[5:]  # strip "args."
        if event.args and key in event.args:
            return event.args[key]
        return None

    return None


# ---------------------------------------------------------------------------
# Python evaluator (runtime backend)
# ---------------------------------------------------------------------------


def eval_predicate(
    pred: Predicate, event: Event, extra: dict[str, Any] | None = None
) -> bool:
    """Evaluate a FOL predicate against a single event.

    Args:
        pred: The FOL predicate to evaluate.
        event: The event to check.
        extra: Extra bindings (e.g. {"_path": "/tmp/foo"} for ForAllPaths).

    Returns:
        True if the predicate holds for this event.
    """
    if isinstance(pred, Equals):
        val = resolve_field(pred.field, event, extra)
        return val == pred.value.value

    if isinstance(pred, Matches):
        val = resolve_field(pred.field, event, extra)
        if not isinstance(val, str):
            return False
        return re.search(pred.pattern, val) is not None

    if isinstance(pred, HasPrefix):
        val = resolve_field(pred.field, event, extra)
        if not isinstance(val, str):
            return False
        return any(val.startswith(p) for p in pred.prefixes)

    if isinstance(pred, InSet):
        val = resolve_field(pred.field, event, extra)
        return val in pred.values

    if isinstance(pred, GreaterThan):
        val = resolve_field(pred.field, event, extra)
        if val is None:
            return False
        return float(val) > pred.threshold

    if isinstance(pred, LessThanEq):
        val = resolve_field(pred.field, event, extra)
        if val is None:
            return False
        return float(val) <= pred.threshold

    if isinstance(pred, PNot):
        return not eval_predicate(pred.child, event, extra)

    if isinstance(pred, PAnd):
        return eval_predicate(pred.left, event, extra) and eval_predicate(
            pred.right, event, extra
        )

    if isinstance(pred, POr):
        return eval_predicate(pred.left, event, extra) or eval_predicate(
            pred.right, event, extra
        )

    if isinstance(pred, PImplies):
        if not eval_predicate(pred.guard, event, extra):
            return True  # vacuously true
        return eval_predicate(pred.body, event, extra)

    if isinstance(pred, ForAllPaths):
        val = resolve_field(pred.field, event, extra)
        if not isinstance(val, str):
            return True  # no value → vacuously true
        paths = re.findall(r"(/[^\s;|&>\"']+)", val)
        if not paths:
            return True  # no paths → vacuously true
        for path in paths:
            bound_extra = dict(extra or {})
            bound_extra["_path"] = path
            if not eval_predicate(pred.path_predicate, event, bound_extra):
                return False
        return True

    raise TypeError(f"Unknown predicate type: {type(pred).__name__}")
