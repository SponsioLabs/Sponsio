"""Immutable AST nodes for the Sponsio formula language.

Three families of nodes, all composable via operator overloading
(``>>`` = implies, ``&`` = and, ``|`` = or, ``~`` = not):

1. **Propositional**: ``Atom``, ``Not``, ``And``, ``Or``, ``Implies``
   -- boolean logic over grounded predicates.
2. **Temporal (LTL)**: ``G``, ``F``, ``X``, ``U``
   -- ordering and liveness over finite traces.
3. **Arithmetic / Set**: ``Le``, ``Lt``, ``Ge``, ``Gt``, ``Eq``,
   ``Var``, ``Const``, ``Subset`` -- numeric constraints (SMT-ready).

Every ``Atom`` produces a canonical string key via ``pred_key()``
(defined in ``_pred_key.py``).  The evaluator looks up this key in the
grounded valuation dict.  The grounding module produces keys using the
same ``pred_key()`` function, so the two sides always agree.

All nodes are frozen dataclasses (immutable, hashable).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Union

from sponsio.formulas._pred_key import pred_key


# ---------------------------------------------------------------------------
# Mixin: operator overloading for composing formulas
# ---------------------------------------------------------------------------


class FormulaMixin:
    """Mixin providing operator overloading for formula composition.

    Enables writing ``f1 >> f2`` (implies), ``f1 & f2`` (and),
    ``f1 | f2`` (or), and ``~f1`` (not).
    """

    def __rshift__(self, other: Formula) -> Implies:
        return Implies(self, other)  # type: ignore[arg-type]

    def __and__(self, other: Formula) -> And:
        return And(self, other)  # type: ignore[arg-type]

    def __or__(self, other: Formula) -> Or:
        return Or(self, other)  # type: ignore[arg-type]

    def __invert__(self) -> Not:
        return Not(self)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Propositional nodes (SAT family)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Atom(FormulaMixin):
    """Atomic predicate — the leaf node of a formula.

    Examples: ``called("fraud_check")``, ``precedes("A", "B")``.

    Attributes:
        predicate: Name of the predicate (e.g. ``"called"``).
        args: Positional arguments to the predicate.
        desc: Optional human-readable description.
        atom_type: ``"det"`` (default) or ``"sto"``. Det atoms ground to
            bool and are evaluated by the LTL/DFA evaluator. Sto atoms
            ground to float in [0,1] via a registered evaluator and flow
            through ``eval_sto_confidence`` lifting. The det execution
            path does NOT read this field, so existing det contracts are
            unaffected.
        output_type: For sto atoms only — ``"classify"`` (yes/no with
            confidence) or ``"score"`` (continuous magnitude).
        context_scope: For sto atoms only — what slice of the trace the
            evaluator needs: single event, last k, full trace, or
            input-output bundle.
        context_k: For sto atoms with ``context_scope="last_k"``.
        prompt_override: For sto atoms only — a domain-specific yes/no
            question that replaces the evaluator's built-in prompt. The
            generic prompts in ``sto_catalog`` target single-turn QA; for
            domain-specific use (e.g. customer-service SOP compliance)
            they tend to over-fire. Pass a tailored prompt here to
            narrow the judge's question without having to register a new
            atom. Non-sto atoms ignore this field.
    """

    predicate: str
    args: tuple[str, ...]
    desc: str = ""
    atom_type: Literal["det", "sto"] = "det"
    output_type: Literal["classify", "score"] | None = None
    context_scope: Literal["event", "last_k", "full_trace", "io_bundle"] | None = None
    context_k: int | None = None
    prompt_override: str | None = None

    def __init__(
        self,
        predicate: str,
        *args: str,
        desc: str = "",
        atom_type: Literal["det", "sto"] = "det",
        output_type: Literal["classify", "score"] | None = None,
        context_scope: Literal["event", "last_k", "full_trace", "io_bundle"]
        | None = None,
        context_k: int | None = None,
        prompt_override: str | None = None,
    ):
        object.__setattr__(self, "predicate", predicate)
        object.__setattr__(self, "args", args)
        object.__setattr__(self, "desc", desc)
        object.__setattr__(self, "atom_type", atom_type)
        object.__setattr__(self, "output_type", output_type)
        object.__setattr__(self, "context_scope", context_scope)
        object.__setattr__(self, "context_k", context_k)
        object.__setattr__(self, "prompt_override", prompt_override)

    def __repr__(self) -> str:
        args_str = ", ".join(repr(a) for a in self.args)
        return f"{self.predicate}({args_str})"

    def key(self) -> str:
        """Returns the canonical string key for grounding lookups.

        Returns:
            A string of the form ``"predicate(arg1, arg2)"``.
        """
        return pred_key(self.predicate, *self.args)


@dataclass(frozen=True)
class Not(FormulaMixin):
    """Logical negation: ``!child``.

    Attributes:
        child: The formula to negate.
    """

    child: Formula

    def __repr__(self) -> str:
        return f"!({self.child})"


@dataclass(frozen=True)
class And(FormulaMixin):
    """Logical conjunction: ``left & right``.

    Attributes:
        left: Left operand.
        right: Right operand.
    """

    left: Formula
    right: Formula

    def __repr__(self) -> str:
        return f"({self.left} & {self.right})"


@dataclass(frozen=True)
class Or(FormulaMixin):
    """Logical disjunction: ``left | right``.

    Attributes:
        left: Left operand.
        right: Right operand.
    """

    left: Formula
    right: Formula

    def __repr__(self) -> str:
        return f"({self.left} | {self.right})"


@dataclass(frozen=True)
class Implies(FormulaMixin):
    """Logical implication: ``left -> right``.

    Attributes:
        left: Antecedent.
        right: Consequent.
    """

    left: Formula
    right: Formula

    def __repr__(self) -> str:
        return f"({self.left} -> {self.right})"


# ---------------------------------------------------------------------------
# Temporal nodes (LTL family)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class G(FormulaMixin):
    """Globally / Always — G(φ) means φ holds at every future timestep."""

    child: Formula

    def __repr__(self) -> str:
        return f"G({self.child})"


@dataclass(frozen=True)
class F(FormulaMixin):
    """Finally / Eventually — F(φ) means φ holds at some future timestep."""

    child: Formula

    def __repr__(self) -> str:
        return f"F({self.child})"


@dataclass(frozen=True)
class X(FormulaMixin):
    """Next — X(φ) means φ holds at the next timestep."""

    child: Formula

    def __repr__(self) -> str:
        return f"X({self.child})"


@dataclass(frozen=True)
class U(FormulaMixin):
    """Until — φ U ψ means φ holds until ψ becomes true."""

    left: Formula
    right: Formula

    def __repr__(self) -> str:
        return f"({self.left} U {self.right})"


# ---------------------------------------------------------------------------
# Arithmetic / Set nodes (SMT family) + Term abstraction
# ---------------------------------------------------------------------------
#
# Comparison nodes (Eq, Le, Lt, Ge, Gt) accept any ``Term`` on either
# side. A Term is anything that knows how to ``evaluate(state) -> value``;
# the evaluator dispatches polymorphically. This lets contract authors
# compose runtime-bound values (``ArgValue("issue_refund", "amount")``)
# against constants (``Const(50)``) or against other runtime values
# (``CtxValue("approved_amount")``) in the same comparison.
#
# Returning ``None`` from ``evaluate`` is the canonical "missing" signal
# — comparison evaluation treats either operand being ``None`` as False
# (the comparison can't decide), so contract authors should wrap fragile
# comparisons in an ``Implies(scope_predicate, comparison)`` to suppress
# them where the relevant arg isn't applicable.
#
# Subclasses provided in this module:
#
# * ``Const(value)`` — literal numeric value.
# * ``Var(name, *args)`` — predicate-key lookup (existing semantics
#   preserved; defaults to 0 on missing for counter-style variables).
# * ``ArgValue(tool, field)`` — raw value of ``args[field]`` when the
#   current event is a call to ``tool``.
# * ``CtxValue(key)`` — raw value of an externally pushed context fact.
# * ``UnaryFn(fn, arg)`` — apply a Python callable to another Term's
#   value (e.g. ``UnaryFn(len, ArgValue("tool", "field"))``).
# * ``ArgLength(tool, field)`` — convenience helper for
#   ``UnaryFn(len, ArgValue(tool, field))``.
#
# Backward compatibility: callers that build ``Eq(Var("count", "x"),
# Const(5))`` continue to work unchanged — ``Var`` and ``Const`` are
# Term subclasses and the existing arith resolver keeps a fast path for
# them.


class Term:
    """Base class for value-producing expressions used in comparisons.

    Subclasses must implement ``evaluate(state) -> object | None``.

    ``None`` is the canonical "missing" signal — comparison evaluation
    treats either operand being ``None`` as False (the comparison can't
    decide), so contract authors should wrap fragile comparisons in an
    ``Implies(scope_predicate, comparison)`` to suppress them where the
    relevant arg isn't applicable.
    """

    def evaluate(self, state: dict) -> object:  # pragma: no cover - abstract
        raise NotImplementedError(
            f"{type(self).__name__} must implement evaluate(state)"
        )


@dataclass(frozen=True)
class Var(FormulaMixin, Term):
    """A numeric or set variable for arithmetic formulas.

    Examples: ``Var("cost")``, ``Var("count", "tool")``.

    Note: ``==`` / ``<`` / ``<=`` / ``>`` / ``>=`` are overloaded to
    *build comparison AST nodes* (``Var("x") == 5`` returns
    ``Eq(Var("x"), Const(5))``), SQLAlchemy-column style — they do NOT
    return a bool. So ``Var("x") == Var("x")`` is a truthy ``Eq`` node,
    not ``True``; don't rely on ``==`` to value-compare two ``Var``
    instances or to dedupe them in ordinary code. Hashing still works
    (the frozen-dataclass ``__hash__`` is based on ``name``/``args``),
    so ``Var`` is usable as a dict key / set member.

    Attributes:
        name: Variable name.
        args: Optional positional arguments for parameterized variables.
    """

    name: str
    args: tuple[str, ...] = ()

    def __init__(self, name: str, *args: str):
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "args", args)

    def __repr__(self) -> str:
        if self.args:
            args_str = ", ".join(repr(a) for a in self.args)
            return f"Var({self.name!r}, {args_str})"
        return f"Var({self.name!r})"

    def key(self) -> str:
        """Returns the canonical lookup key for this variable.

        Returns:
            ``"name"`` or ``"name(arg1, arg2)"`` if parameterized.
        """
        if self.args:
            return pred_key(self.name, *self.args)
        return self.name

    def evaluate(self, state: dict) -> object:
        """Term protocol — counter-style lookup with 0 default.

        Preserves the pre-Term semantics: numeric value if present,
        ``0`` if the variable is missing (so unevaluated counters
        compare as zero, matching the long-standing convention for
        ``count(tool)``-style arithmetic).
        """
        key = self.key()
        val = state.get(key)
        if isinstance(val, (int, float)):
            return val
        return 0

    # Comparison operators — return AST nodes so repr() is round-trippable.
    def _coerce_term(self, other: object) -> "Term":
        """Coerce ``other`` into a Term: pass Terms through, wrap int/float in Const."""
        if isinstance(other, Term):
            return other
        return Const(other)  # type: ignore[arg-type]

    def __le__(self, other):  # type: ignore[override]
        return Le(self, self._coerce_term(other))

    def __lt__(self, other):  # type: ignore[override]
        return Lt(self, self._coerce_term(other))

    def __ge__(self, other):  # type: ignore[override]
        return Ge(self, self._coerce_term(other))

    def __gt__(self, other):  # type: ignore[override]
        return Gt(self, self._coerce_term(other))

    def __eq__(self, other):  # type: ignore[override]
        if isinstance(other, (int, float, Term)):
            return Eq(self, self._coerce_term(other))
        return NotImplemented


@dataclass(frozen=True)
class Const(Term):
    """A constant numeric value."""

    value: int | float

    def __repr__(self) -> str:
        return str(self.value)

    def evaluate(self, _state: dict) -> object:
        return self.value


# Generic "anything that produces a value" — Var, Const, or any other
# Term subclass (ArgValue, CtxValue, UnaryFn, ArgLength, …). Kept as the
# alias ``ArithExpr`` for backward compatibility with type hints in
# downstream code.
ArithExpr = Term


@dataclass(frozen=True)
class ArgValue(Term):
    """Read ``args[field]`` from the current event when it is a call to ``tool``.

    Returns ``None`` (treated as "missing" by the comparison
    evaluator) when:

    * the current event is a call to a different tool,
    * the current event is not a tool call at all,
    * ``args[field]`` is not present.

    Pair with ``Implies(called(tool), ...)`` to scope the rule cleanly.
    """

    tool: str
    field: str

    def __repr__(self) -> str:
        return f"ArgValue({self.tool!r}, {self.field!r})"

    def evaluate(self, state: dict) -> object:
        # Grounding (sponsio.tracer.grounding) pushes the raw arg value
        # under this key on tool_call events. See ``ground_event``.
        return state.get(pred_key("arg_value", self.tool, self.field))


@dataclass(frozen=True)
class CtxValue(Term):
    """Read a fact pushed via ``guard.observe_context({key: value})``.

    Returns ``None`` when the key has never been pushed for this trace.
    """

    key: str

    def __repr__(self) -> str:
        return f"CtxValue({self.key!r})"

    def evaluate(self, state: dict) -> object:
        return state.get(pred_key("ctx_value", self.key))


@dataclass(frozen=True)
class UnaryFn(Term):
    """Apply a Python callable to another Term's value.

    Common use cases::

        ArgLength = UnaryFn(len, ArgValue("book_reservation", "passengers"))
        Lower     = UnaryFn(str.lower, ArgValue("send_email", "subject"))
        Abs       = UnaryFn(abs, Var("balance"))

    If the inner Term resolves to ``None``, ``UnaryFn`` also returns
    ``None``. If the callable raises ``TypeError`` / ``ValueError``
    (e.g. ``len`` of a non-collection), ``UnaryFn`` returns ``None``
    rather than crashing — comparison evaluation will then treat the
    operand as missing and short-circuit to False.

    The ``name`` field is for ``repr()`` only and defaults to the
    callable's ``__name__`` attribute.
    """

    fn: object  # callable; ``object`` to keep dataclass-frozen hashable
    arg: Term
    name: str = ""

    def __repr__(self) -> str:
        n = self.name or getattr(self.fn, "__name__", "fn")
        return f"{n}({self.arg!r})"

    def evaluate(self, state: dict) -> object:
        v = self.arg.evaluate(state)
        if v is None:
            return None
        try:
            return self.fn(v)  # type: ignore[operator]
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True)
class ArgLength(Term):
    """Convenience: ``len(args[field])`` for the current event.

    Equivalent to ``UnaryFn(len, ArgValue(tool, field))`` but exposed
    as its own class for cleaner repr and slightly faster evaluation.
    Returns ``None`` when the arg is missing or not a sized container.
    """

    tool: str
    field: str

    def __repr__(self) -> str:
        return f"ArgLength({self.tool!r}, {self.field!r})"

    def evaluate(self, state: dict) -> object:
        v = state.get(pred_key("arg_value", self.tool, self.field))
        if v is None:
            return None
        try:
            return len(v)  # type: ignore[arg-type]
        except TypeError:
            return None


@dataclass(frozen=True)
class Le(FormulaMixin):
    """Less than or equal: left <= right."""

    left: Term
    right: Term

    def __repr__(self) -> str:
        return f"({self.left} <= {self.right})"


@dataclass(frozen=True)
class Lt(FormulaMixin):
    """Strictly less than: left < right."""

    left: Term
    right: Term

    def __repr__(self) -> str:
        return f"({self.left} < {self.right})"


@dataclass(frozen=True)
class Ge(FormulaMixin):
    """Greater than or equal: left >= right."""

    left: Term
    right: Term

    def __repr__(self) -> str:
        return f"({self.left} >= {self.right})"


@dataclass(frozen=True)
class Gt(FormulaMixin):
    """Strictly greater than: left > right."""

    left: Term
    right: Term

    def __repr__(self) -> str:
        return f"({self.left} > {self.right})"


@dataclass(frozen=True)
class Eq(FormulaMixin):
    """Equality: left == right."""

    left: Term
    right: Term

    def __repr__(self) -> str:
        return f"({self.left} == {self.right})"


@dataclass(frozen=True)
class Subset(FormulaMixin):
    """Set inclusion: left ⊆ right."""

    left: str
    right: str

    def __repr__(self) -> str:
        return f"subset({self.left}, {self.right})"

    def key(self) -> str:
        return pred_key("subset", self.left, self.right)


# ---------------------------------------------------------------------------
# Union type
# ---------------------------------------------------------------------------

Formula = Union[
    # Propositional
    Atom,
    Not,
    And,
    Or,
    Implies,
    # Temporal (LTL)
    G,
    F,
    X,
    U,
    # Arithmetic / Set (SMT)
    Le,
    Lt,
    Ge,
    Gt,
    Eq,
    Subset,
]


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def collect_atoms(formula: Formula) -> set[Atom]:
    """Recursively collects all ``Atom`` nodes from a formula tree.

    Args:
        formula: The root formula to traverse.

    Returns:
        A set of all ``Atom`` instances found in the tree.
    """
    if isinstance(formula, Atom):
        return {formula}
    elif isinstance(formula, Not):
        return collect_atoms(formula.child)
    elif isinstance(formula, (And, Or, Implies, U)):
        return collect_atoms(formula.left) | collect_atoms(formula.right)
    elif isinstance(formula, (G, F, X)):
        return collect_atoms(formula.child)
    # Arithmetic nodes don't contain Atoms
    return set()
