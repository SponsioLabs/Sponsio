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
from typing import Union

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
    """

    predicate: str
    args: tuple[str, ...]
    desc: str = ""

    def __init__(self, predicate: str, *args: str, desc: str = ""):
        object.__setattr__(self, "predicate", predicate)
        object.__setattr__(self, "args", args)
        object.__setattr__(self, "desc", desc)

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
# Arithmetic / Set nodes (SMT family)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Var(FormulaMixin):
    """A numeric or set variable for arithmetic formulas.

    Examples: ``Var("cost")``, ``Var("count", "tool")``.

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

    # Comparison operators — return AST nodes so repr() is round-trippable.
    def __le__(self, other):  # type: ignore[override]
        return Le(self, other if isinstance(other, (Var, Const)) else Const(other))

    def __lt__(self, other):  # type: ignore[override]
        return Lt(self, other if isinstance(other, (Var, Const)) else Const(other))

    def __ge__(self, other):  # type: ignore[override]
        return Ge(self, other if isinstance(other, (Var, Const)) else Const(other))

    def __gt__(self, other):  # type: ignore[override]
        return Gt(self, other if isinstance(other, (Var, Const)) else Const(other))

    def __eq__(self, other):  # type: ignore[override]
        if isinstance(other, (int, float, Const)):
            return Eq(self, other if isinstance(other, (Var, Const)) else Const(other))
        return NotImplemented


@dataclass(frozen=True)
class Const:
    """A constant numeric value."""

    value: int | float

    def __repr__(self) -> str:
        return str(self.value)


# Arithmetic expression type: Var or Const
ArithExpr = Union[Var, Const]


@dataclass(frozen=True)
class Le(FormulaMixin):
    """Less than or equal: left <= right."""

    left: ArithExpr
    right: ArithExpr

    def __repr__(self) -> str:
        return f"({self.left} <= {self.right})"


@dataclass(frozen=True)
class Lt(FormulaMixin):
    """Strictly less than: left < right."""

    left: ArithExpr
    right: ArithExpr

    def __repr__(self) -> str:
        return f"({self.left} < {self.right})"


@dataclass(frozen=True)
class Ge(FormulaMixin):
    """Greater than or equal: left >= right."""

    left: ArithExpr
    right: ArithExpr

    def __repr__(self) -> str:
        return f"({self.left} >= {self.right})"


@dataclass(frozen=True)
class Gt(FormulaMixin):
    """Strictly greater than: left > right."""

    left: ArithExpr
    right: ArithExpr

    def __repr__(self) -> str:
        return f"({self.left} > {self.right})"


@dataclass(frozen=True)
class Eq(FormulaMixin):
    """Equality: left == right."""

    left: ArithExpr
    right: ArithExpr

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
