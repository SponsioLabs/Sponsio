"""Contract dataclass — one assume/enforcement pair for an agent.

A ``Contract`` binds a single ``assumption`` (precondition over the trace)
to a single ``enforcement`` (what the agent must satisfy when the
assumption holds). An agent with multiple independent rules has multiple
``Contract`` entries — ``System.contracts`` is already a flat list, so
no new container type is needed.

Both ``assumption`` and ``enforcement`` accept either a single constraint
or a list. A list is interpreted as the logical AND of its elements.
``assumption=None`` (the default) means the contract is unconditional.

Stochastic semantics
--------------------

Contracts carry two threshold fields for stochastic enforcement:

* ``alpha`` (default 1.0) — the assumption triggers when
  ``conf(A) ≥ alpha``. For pure det assumptions, ``conf(A)`` is strictly
  0.0 or 1.0, so any ``alpha ∈ (0, 1]`` behaves the same.
* ``beta`` (default 1.0) — the enforcement is satisfied when
  ``conf(G) ≥ beta`` (given the assumption is triggered). Default 1.0
  preserves existing det semantics — an enforcement that evaluates to
  ``True`` (confidence 1.0) passes; anything less fails.

See ``docs/cost-based-thresholds.md`` for how to pick ``(alpha, beta)``
from operational costs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sponsio.models.agent import Agent

# A constraint is a hard formula, a sto constraint, or a list of either
Constraint = Any  # Formula | DetFormula | StoFormula | list[...]

# ANSI helpers
_BOLD = "1"
_GREEN = "32"
_YELLOW = "33"
_DIM = "2"


def _ansi(code: str, text: str, colorize: bool) -> str:
    if not colorize:
        return text
    return f"\033[{code}m{text}\033[0m"


def _as_list(value: Any) -> list:
    """Normalize a scalar / list / None to a flat list."""
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    return [value]


def _unwrap(item: Any) -> Any:
    """Extract the underlying ``Formula`` from a constraint object.

    Constraints may be raw ``Formula`` instances, ``DetFormula`` /
    ``StoFormula`` wrappers (with a ``.formula`` attribute), or other
    shapes. Returns ``None`` if the item doesn't carry a ``Formula``.
    """
    from sponsio.formulas.formula import FormulaMixin

    if isinstance(item, FormulaMixin):
        return item
    inner = getattr(item, "formula", None)
    if isinstance(inner, FormulaMixin):
        return inner
    return None


# ``eq=False`` keeps Contract hashable by identity. The runtime monitor
# uses Contracts as keys in a ``WeakKeyDictionary`` for the per-contract
# atom cache (``RuntimeMonitor._atom_caches``), and a default
# ``@dataclass`` (eq=True, frozen=False) sets ``__hash__`` to None — that
# would break the cache. We don't use Contract value-equality anywhere,
# so falling back to ``object.__eq__`` is safe.
@dataclass(eq=False)
class Contract:
    """A single assume/enforcement pair bound to an agent.

    Attributes:
        agent: The agent this contract belongs to.
        enforcement: What the agent must satisfy. Required. May be a
            single constraint or a list (list = logical AND).
        assumption: Precondition over the trace. ``None`` means the
            contract is unconditional. May be a single constraint or a
            list (list = logical AND).
        desc: Optional human-readable label for this contract.
        alpha: Assumption trigger threshold in [0, 1]. Default 1.0 —
            preserves existing det semantics.
        beta: Enforcement satisfaction threshold in [0, 1]. Default 1.0
            — preserves existing det semantics.
        activate_at: When the assumption A is satisfied, *where* the
            enforcement E should start being checked.

            * ``None`` (default) — global semantics: A and E are each
              evaluated as standalone LTL over the full trace from
              position 0.  If A holds, every position must satisfy E
              including positions before A's "evidence event".  This is
              the historical Sponsio semantic — appropriate for global
              invariants ("if user is admin throughout, then every
              read is logged").
            * ``"first_match"`` — reactive semantics: find the first
              position k where A becomes true (its evidence event), then
              evaluate E starting at position k.  Events before k are
              not subject to E.  Appropriate for trigger-then-enforce
              safety contracts ("after secret is read, no outbound
              POST" should not retroactively flag a POST that happened
              before the secret read).

              Supported assumption shapes for ``first_match``:
                - ``F(φ)``       — activation k = first position where φ holds
                - ``Atom``       — activation k = first position where the
                                    atom holds
                - list of those — activation k = max of each assumption's
                                    first-match (the latest one to fire)
              Other shapes (``G(φ)``, ``φ U ψ``, arithmetic comparisons,
              …) are rejected at __post_init__ time so the user gets a
              clear error rather than silently mis-specified semantics.
    """

    agent: Agent
    enforcement: Constraint = None
    assumption: Constraint | None = None
    desc: str | None = None
    alpha: float = 1.0
    beta: float = 1.0
    activate_at: str | None = None

    _VALID_ACTIVATE_AT = (None, "first_match")

    def __post_init__(self) -> None:
        if self.enforcement is None or (
            isinstance(self.enforcement, list) and not self.enforcement
        ):
            raise ValueError(
                f"Contract(agent={self.agent.id!r}) requires a non-empty enforcement. "
                f"Use Contract(..., enforcement=<constraint>) or provide a list."
            )
        if not (0.0 <= self.alpha <= 1.0):
            raise ValueError(
                f"Contract(agent={self.agent.id!r}): alpha must be in [0, 1], "
                f"got {self.alpha!r}"
            )
        if not (0.0 <= self.beta <= 1.0):
            raise ValueError(
                f"Contract(agent={self.agent.id!r}): beta must be in [0, 1], "
                f"got {self.beta!r}"
            )
        if self.activate_at not in self._VALID_ACTIVATE_AT:
            raise ValueError(
                f"Contract(agent={self.agent.id!r}): activate_at must be one of "
                f"{self._VALID_ACTIVATE_AT!r}, got {self.activate_at!r}"
            )
        if self.activate_at == "first_match":
            if self.assumption is None:
                raise ValueError(
                    f"Contract(agent={self.agent.id!r}): activate_at='first_match' "
                    f"requires a non-None assumption (there is nothing to activate)."
                )
            self._validate_first_match_assumption_shape()

    def _validate_first_match_assumption_shape(self) -> None:
        """Reject assumptions whose ``first_match`` semantics are unclear.

        ``first_match`` is well-defined for ``F(φ)`` (activation = first
        position where φ holds) and for atomic predicates (same).  It
        is *not* well-defined for ``G(φ)`` (which can only become true
        at end-of-trace) or arithmetic comparisons over counters.  We
        reject the unsupported shapes at construction time rather than
        silently treating them as a per-position re-evaluation.
        """
        from sponsio.formulas.formula import Atom, F
        from sponsio.patterns.library import DetFormula

        def _check(constraint: Any, idx: int) -> None:
            if not hasattr(constraint, "formula"):
                # Sto / non-DetFormula assumption — sto pipeline owns it.
                return
            raw = (
                constraint.formula if isinstance(constraint, DetFormula) else constraint
            )
            if isinstance(raw, (F, Atom)):
                return
            raise ValueError(
                f"Contract(agent={self.agent.id!r}): activate_at='first_match' "
                f"only supports F(φ) or atomic assumptions; assumption[{idx}] "
                f"has shape {type(raw).__name__}. Use the default global "
                f"semantics (omit activate_at) or rewrite the assumption."
            )

        for i, a in enumerate(self.assumptions):
            _check(a, i)

    # -----------------------------------------------------------------
    # Atom-type introspection (for runtime dispatch)
    # -----------------------------------------------------------------

    @property
    def is_pure_det(self) -> bool:
        """True iff every atom in assumption and enforcement has
        ``atom_type == "det"`` AND ``alpha == beta == 1.0``.

        When true, the monitor can dispatch to the existing LTL/DFA
        evaluator without paying the probabilistic-lifting overhead.
        """
        if self.alpha != 1.0 or self.beta != 1.0:
            return False
        # ``_all_det`` lives in the (cloud) sto-lifting module; the OSS
        # engine ships only the deterministic pipeline. Best-effort
        # import keeps the cloud path live when both packages are
        # installed; absent it, every contract that reached this point
        # is by definition pure-det (sto formula construction needs the
        # cloud module too).
        try:  # pragma: no cover - guarded import
            from sponsio.runtime.sto_lifting import _all_det  # type: ignore[import-not-found]
        except ImportError:
            return True

        for item in self.enforcements + self.assumptions:
            inner = _unwrap(item)
            if inner is None:
                # Unknown / non-Formula constraint (e.g. raw NL string that
                # hasn't been compiled yet) — conservatively force lifting
                # path. Actual dispatch decision happens after compilation.
                return False
            if not _all_det(inner):
                return False
        return True

    # -----------------------------------------------------------------
    # Normalized views (plural properties)
    # -----------------------------------------------------------------

    @property
    def assumptions(self) -> list:
        """Assumption as a flat list (empty if unconditional).

        The singular ``.assumption`` field holds the canonical value
        (scalar, list, or ``None``); this property normalizes it to a
        list for iteration.
        """
        return _as_list(self.assumption)

    @property
    def enforcements(self) -> list:
        """Enforcement as a flat list.

        The singular ``.enforcement`` field holds the canonical value
        (scalar or list); this property normalizes it to a list for
        iteration.
        """
        return _as_list(self.enforcement)

    @property
    def is_unconditional(self) -> bool:
        return not self.assumptions

    # -----------------------------------------------------------------
    # Pretty printing
    # -----------------------------------------------------------------

    def to_str(self, colorize: bool = False, show_compiled: bool = True) -> str:
        """Human-readable A/E representation.

        Args:
            colorize: Emit ANSI color codes.
            show_compiled: Emit a dim ``compiled:`` line beneath each author
                description with the LTL-derived NL (via
                :func:`sponsio.formulas.nl_gen.formula_to_nl`). Surfaces drift
                between what the author wrote and what the engine actually
                enforces. Falls back silently if the underlying AST is not
                introspectable.
        """
        bar = _ansi(_DIM, "\u258e", colorize)

        agent_name = _ansi(_BOLD, self.agent.id, colorize)
        lines = [f"{bar} {_ansi(_BOLD, 'contract', colorize)} \u00b7 {agent_name}"]
        if self.desc:
            lines.append(f"{bar} {_ansi(_DIM, self.desc, colorize)}")
        lines.append(f"{bar} ")

        a_tri = _ansi(_YELLOW, "\u25b8", colorize)
        e_tri = _ansi(_GREEN, "\u25b8", colorize)
        blank = " " * 8

        def _compiled_line(item) -> str | None:
            """Return the dim `compiled: <formula_to_nl>` line, or None."""
            if not show_compiled:
                return None
            try:
                from sponsio.formulas.formula import FormulaMixin
                from sponsio.formulas.nl_gen import formula_to_nl
            except ImportError:
                return None
            formula = (
                item
                if isinstance(item, FormulaMixin)
                else getattr(item, "formula", None)
            )
            if formula is None:
                return None
            try:
                nl = formula_to_nl(formula).strip()
            except Exception:
                return None
            if not nl:
                return None
            # ``desc`` is normally a string but YAML round-trips can
            # surface a list (e.g. ``args: [...]`` mis-parsed as desc on
            # malformed entries) — coerce so the dedup against ``nl``
            # below doesn't crash with `'list' object has no attribute
            # 'strip'`.  Best-effort: stringify, falling back to "".
            desc = getattr(item, "desc", "") or ""
            if isinstance(desc, list):
                desc = " ".join(str(x) for x in desc)
            elif not isinstance(desc, str):
                desc = str(desc)
            if nl == desc.strip():
                return None
            return f"{bar} {blank}{_ansi(_DIM, f'compiled: {nl}', colorize)}"

        a_label = _ansi(_DIM, "assume  ", colorize)
        if not self.assumptions:
            lines.append(f"{bar} {a_label}{_ansi(_DIM, 'true', colorize)}")
        else:
            for idx, a in enumerate(self.assumptions):
                desc = getattr(a, "desc", str(a))
                prefix = a_label if idx == 0 else blank
                lines.append(f"{bar} {prefix}{a_tri} {desc}")
                c = _compiled_line(a)
                if c is not None:
                    lines.append(c)

        e_label = _ansi(_DIM, "enforce ", colorize)
        for idx, e in enumerate(self.enforcements):
            desc = getattr(e, "desc", str(e))
            prefix = e_label if idx == 0 else blank
            lines.append(f"{bar} {prefix}{e_tri} {desc}")
            c = _compiled_line(e)
            if c is not None:
                lines.append(c)

        return "\n".join(lines)

    def __str__(self) -> str:
        return self.to_str(colorize=False)

    def __repr__(self) -> str:
        a_count = len(self.assumptions)
        e_count = len(self.enforcements)
        return (
            f"Contract(agent={self.agent.id!r}, "
            f"assumption={a_count}, enforcement={e_count})"
        )


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def make_contracts(
    agent: Agent,
    *,
    enforcements: list | None = None,
    contracts: list[dict] | None = None,
) -> list[Contract]:
    """Build a list of ``Contract`` objects from the two main input shapes.

    Args:
        agent: The agent all contracts belong to.
        enforcements: Shortcut for unconditional contracts. Each item
            becomes one ``Contract(agent, enforcement=item)`` with no
            assumption. Useful for the simple "list of rules" case.
        contracts: List of dicts, each with ``assumption`` (optional)
            and ``enforcement`` (required). Each dict becomes one
            ``Contract``. ``assumption`` / ``enforcement`` may be a
            scalar or a list; lists are preserved for later AND-combine.

    Returns:
        A flat list of ``Contract`` objects, ready for
        ``System._contracts.extend(...)``.
    """
    out: list[Contract] = []

    for item in enforcements or []:
        out.append(Contract(agent=agent, enforcement=item))

    for entry in contracts or []:
        if not isinstance(entry, dict):
            raise TypeError(
                f"contracts[] entries must be dict, got {type(entry).__name__}: {entry!r}"
            )
        enforcement = entry.get("enforcement") or entry.get("E")
        if enforcement is None:
            raise ValueError(
                f"Contract entry missing 'enforcement' (or 'E'): {entry!r}"
            )
        assumption = entry.get("assumption", entry.get("A"))
        desc = entry.get("desc")
        alpha = entry.get("alpha", 1.0)
        beta = entry.get("beta", 1.0)
        out.append(
            Contract(
                agent=agent,
                enforcement=enforcement,
                assumption=assumption,
                desc=desc,
                alpha=alpha,
                beta=beta,
            )
        )

    return out
