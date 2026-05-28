"""Formal LTL verifier — the pure "does this trace satisfy this formula?" layer.

``Verifier`` is a stateful wrapper around the two pieces of the formal
pipeline:

1. :func:`sponsio.tracer.grounding.ground` — translates a ``Trace`` into
   per-timestep predicate valuations.
2. :func:`sponsio.formulas.evaluator.evaluate` — runs LTL / arithmetic
   evaluation on those valuations.

The runtime monitor owns one ``TraceVerifier`` per session and delegates
all formal work to it. Callers who just want to **query** whether a
formula holds on a trace — without involving enforcement strategies,
spans, or trace mutation — can use a ``TraceVerifier`` directly::

    from sponsio.runtime.verifier import TraceVerifier
    from sponsio.patterns.library import must_precede

    v = TraceVerifier()
    v.sync(trace)                                 # ground the trace
    v.check(must_precede("verify", "transfer"))   # Verdict(holds=True|False, desc=...)

The canonical input type for ``check`` is a raw LTL-family ``Formula``
AST (``G``/``F``/``U``/``X``/``And``/``Or``/``Not``/``Atom``/``Le``/…)
from :mod:`sponsio.formulas.formula`. The pattern library
(:mod:`sponsio.patterns.library`) is a convenience factory that
produces :class:`~sponsio.patterns.library.DetFormula` wrappers around
those raw ASTs — ``check`` accepts them too and unwraps internally.

Outputs are :class:`Verdict` / :class:`ContractVerdict` dataclasses —
pure facts, no ``EnforcementResult``, no side effects, no spans.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from sponsio.formulas.evaluator import evaluate as eval_formula
from sponsio.tracer.grounding import (
    GroundingState,
    collect_content_atoms,
    ground_event,
)

if TYPE_CHECKING:
    from sponsio.models.contract import Contract
    from sponsio.models.trace import Trace


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class Verdict:
    """Result of evaluating a single formula on a trace.

    A pure fact — no enforcement decision, no side effect.

    Attributes:
        holds: Whether the formula is satisfied on the current trace.
        desc: Human-readable description of the formula (from
            ``DetFormula.desc`` or ``str(formula)``).
        kind: ``"assumption"`` or ``"guarantee"``. Defaults to
            ``"guarantee"`` for free-standing ``Verifier.check`` calls.
        formula: The original constraint object (``DetFormula`` or
            raw ``Formula``) kept for reporting / ``Violation``
            construction. ``None`` for trivially-true results.
        score: For sto verdicts — the raw confidence in [0, 1] computed
            by ``eval_sto_confidence``. ``None`` for det verdicts.
        threshold: For sto verdicts — the α (assumption) or β (enforcement)
            threshold the score was compared against. ``None`` for det.
        evidence: Optional one-line explanation from the judge / evaluator
            (e.g. ``"judge answered 'no' (conf=0.42)"``).
        suggestion: Optional fix hint surfaced into retry prompts.
        policy_key: Stable lookup key for the user-configured policy map.
            For det verdicts this equals ``desc``. For sto verdicts ``desc``
            is augmented with ``[conf=..., beta=...]`` for human display,
            but ``policy_key`` keeps the bare ``_describe(constraint, ...)``
            string so ``self._policy[stable_key]`` still resolves the
            user's policy overrides (any RetryWithConstraint /
            RedirectToSafe strategy lands on this same key).
            Empty string falls back to ``desc`` for backward compatibility.
        fresh: Whether the latest trace event itself caused this verdict's
            outcome. Only meaningful for ``holds=False`` guarantees:
            ``fresh=True`` means the just-appended event broke the rule and
            should be blocked; ``fresh=False`` means the violation was
            already present on the prefix (stale) and the current event is
            not the cause. The runtime monitor skips stale enforcement
            violations so a single bad event under observe-mode (or any
            other no-rollback path) is not re-blamed on every subsequent
            event. Defaults to ``True`` so callers that don't set it keep
            their pre-existing block-on-violation behavior.
    """

    holds: bool
    desc: str
    kind: str = "guarantee"
    formula: Any = None
    score: float | None = None
    threshold: float | None = None
    evidence: str = ""
    suggestion: str = ""
    policy_key: str = ""
    fresh: bool = True

    def __bool__(self) -> bool:
        return self.holds

    @property
    def lookup_key(self) -> str:
        """The key to use for ``policy.get(...)`` lookups.

        Returns ``policy_key`` if set, else falls back to ``desc``.
        Use this — never ``desc`` directly — when keying into the
        user-configured strategy policy map.
        """
        return self.policy_key or self.desc

    @property
    def is_sto(self) -> bool:
        """True iff this verdict came from the probabilistic-lifting path.

        Always ``False`` in this build (the lifting path is an
        extension point with no implementation included). An injected
        sto evaluator can set ``score`` on verdicts it produces so its
        own dispatch can pick the sto-aware enforcement path
        (``RetryWithConstraint`` + lesson) over the det ``DetBlock``
        path. Kept in the schema so callers / reporters /
        session-loggers can branch consistently.
        """
        return self.score is not None


@dataclass
class ContractVerdict:
    """Result of evaluating a whole :class:`Contract`.

    Contains one :class:`Verdict` per assumption (short-circuited on
    first failure) and one per guarantee (all evaluated if the
    assumption holds; empty list if assumption failed).

    Use the convenience properties ``holds`` / ``assumption_holds`` /
    ``guarantee_violations`` instead of iterating the raw lists when
    you just want a yes/no.
    """

    assumptions: list[Verdict] = field(default_factory=list)
    guarantees: list[Verdict] = field(default_factory=list)

    @property
    def assumption_holds(self) -> bool:
        """True iff every det assumption evaluated True (or there are none)."""
        return all(r.holds for r in self.assumptions)

    @property
    def first_assumption_failure(self) -> Verdict | None:
        """The first failing assumption, or ``None`` if all hold."""
        for r in self.assumptions:
            if not r.holds:
                return r
        return None

    @property
    def guarantee_violations(self) -> list[Verdict]:
        """Guarantees that evaluated False."""
        return [r for r in self.guarantees if not r.holds]

    @property
    def holds(self) -> bool:
        """True iff assumption holds AND no enforcement is violated."""
        return self.assumption_holds and not self.guarantee_violations

    def __bool__(self) -> bool:
        return self.holds


# ---------------------------------------------------------------------------
# Helpers (duplicated from monitor.py — kept local so Verifier has no
# dependency on monitor.py)
# ---------------------------------------------------------------------------


def _is_det(constraint: Any) -> bool:
    """True if the constraint is a det formula (not a sto evaluator)."""
    from sponsio.patterns.library import DetFormula

    if isinstance(constraint, DetFormula):
        return True
    return not hasattr(constraint, "evaluator_fn")


def _is_det_formula(constraint: Any) -> bool:
    from sponsio.patterns.library import DetFormula

    return isinstance(constraint, DetFormula)


def _raw_formula(constraint: Any) -> Any:
    """Strip ``DetFormula`` wrapper to get the underlying ``Formula`` AST."""
    from sponsio.patterns.library import DetFormula

    if isinstance(constraint, DetFormula):
        return constraint.formula
    return constraint


def _desc(constraint: Any) -> str:
    return getattr(constraint, "desc", str(constraint))


def _collect_det_formulas(contracts: list[Contract]) -> list:
    out: list = []
    for c in contracts:
        for constraint in c.guarantees + c.assumptions:
            if _is_det(constraint):
                out.append(constraint)
    return out


# Cumulative-aggregate Var names emitted by ``sponsio.tracer.grounding``.
# Their valuation at position ``p`` reflects accumulated trace history up
# to ``p`` (count of past calls, depth, sustained time, …) — *not* a
# property of the event at ``p`` alone. The fresh-violation check uses
# this to tell "the latest event moved the aggregate" (fresh) from
# "unrelated event, aggregate carried forward" (stale).
_CUMULATIVE_VAR_NAMES: frozenset[str] = frozenset(
    {
        "count",
        "count_with",
        "token_count",
        "consecutive_count",
        "delegation_depth",
        "time_since",
    }
)


def _collect_cumulative_var_keys(node: Any) -> set[str]:
    """All cumulative-aggregate valuation keys referenced under ``node``.

    Walks the formula AST collecting :class:`Var` nodes whose ``name`` is
    in :data:`_CUMULATIVE_VAR_NAMES`, returning the canonical
    ``Var.key()`` for each. Used by
    :meth:`TraceVerifier._enforcement_is_fresh` to decide whether a
    failing enforcement's responsibility lies with the just-appended
    event (an aggregate moved) or with a prior event whose violation is
    being carried forward (the aggregate didn't move).

    Returns an empty set for purely event-local formulas (only ``Atom``
    leaves), which signals the caller to fall through to per-step
    predicate evaluation.
    """
    from sponsio.formulas.formula import Var

    keys: set[str] = set()
    stack: list[Any] = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, Var) and cur.name in _CUMULATIVE_VAR_NAMES:
            keys.add(cur.key())
            continue
        for attr in ("child", "left", "right"):
            sub = getattr(cur, attr, None)
            if sub is not None:
                stack.append(sub)
    return keys


def _is_temporally_flat(node: Any) -> bool:
    """True if ``node`` contains no nested temporal operator (G/F/U/X).

    The Verifier's G-cache assumes that previously-verified prefix
    positions stay verified as the trace grows. That assumption is
    **wrong** when the G-child contains a nested temporal operator:
    e.g. in ``G(A -> G(!B))``, evaluating the outer G at an old
    position requires re-evaluating the inner G over a now-longer
    suffix. So we only cache G(φ) when φ is temporally flat —
    propositional or arithmetic only.

    Common G-rooted patterns that are temporally flat:

    * ``rate_limit(X, K)``      = ``G(count(X) <= K)``
    * ``scope_limit(X, paths)`` = ``G(arg_paths_within(X, *paths))``
    * ``arg_blacklist(...)``    = ``G(!arg_field_has(...))``
    * ``idempotent(X)``         = ``G(count(X) <= 1)``
    * ``bounded_retry(X, N)``   = ``G(count(X) <= N)``

    ``no_reversal`` / ``must_confirm`` / ``cooldown`` / ``mutual_exclusion``
    contain nested temporal operators and fall through to full eval.
    """
    from sponsio.formulas.formula import F, G, U, X

    if isinstance(node, (G, F, U, X)):
        return False
    for attr in ("child", "left", "right"):
        child = getattr(node, attr, None)
        if child is not None and not _is_temporally_flat(child):
            return False
    return True


# ---------------------------------------------------------------------------
# TraceVerifier
# ---------------------------------------------------------------------------


class TraceVerifier:
    """Stateful LTL verifier over a :class:`Trace`.

    One instance per session / per set of contracts. The verifier owns
    the last-grounded valuations so :meth:`check` and :meth:`check_contract`
    are fast pure lookups; call :meth:`sync` when the trace gains new
    events.

    Args:
        agents: Optional mapping of ``agent_id`` to ``Agent`` objects,
            used by grounding for permission predicates.
        backend: Evaluation strategy. One of:

            - ``"recursive"`` (default) — stateless recursive LTL
              evaluator from :mod:`sponsio.formulas.evaluator` with a
              G-node cache. Battle-tested; used by all existing tests.
            - ``"dfa"`` — formula-progression LTL monitor from
              :mod:`sponsio.formulas.dfa_evaluator`. Handles nested
              temporal operators (``no_reversal``, ``mutual_exclusion``)
              without the flatness guard the recursive backend needs.
              Stateful per-formula: each formula gets its own
              :class:`~sponsio.formulas.dfa_evaluator.DFAEvaluator` that
              is stepped forward as events arrive, giving O(1)
              per-event cost regardless of trace length or formula
              complexity.

            Both backends produce identical :class:`Verdict` objects
            for the same ``(formula, trace)`` input — ``"dfa"`` is a
            drop-in replacement. The recursive backend stays as ground
            truth for differential testing.

    Typical callers:

    * :class:`sponsio.runtime.monitor.RuntimeMonitor` wraps one of these
      internally and syncs after every ``check_action`` mutation.
    * Ad-hoc scripts / offline checks: ``v = TraceVerifier(); v.sync(trace); v.check(f)``.
    """

    def __init__(
        self,
        agents: dict[str, Any] | None = None,
        backend: Literal["recursive", "dfa"] = "recursive",
    ) -> None:
        self._agents: dict[str, Any] = agents or {}
        self._valuations: list[dict] = []
        self._state = GroundingState()
        self._grounded_upto: int = 0
        # Cached content_atoms from the last sync. If the caller passes
        # a different value, we re-ground from scratch because different
        # content atoms mean different per-event work.
        self._last_content_atoms: dict | None = None
        # Per-``G`` node memo: G_node -> (scanned_upto, result)
        # Used only by the ``"recursive"`` backend. We use the
        # formula object itself as the key (frozen dataclasses are
        # hashable) rather than id() — otherwise Python GC can reuse
        # an id after the original formula is collected, leading to
        # a stale cache hit on a structurally different formula.
        self._g_cache: dict[Any, tuple[int, bool]] = {}
        # --- DFA backend state ---
        self._backend: Literal["recursive", "dfa"] = backend
        # Per-formula DFAEvaluator instances, lazily constructed the
        # first time a formula is checked. Keyed by the formula object
        # itself (hashable frozen dataclass) — structurally equal
        # formulas share a DFA, which is both correct and an extra win.
        self._dfas: dict[Any, Any] = {}
        # How many valuations each DFA has already consumed. Used by
        # ``_dfa_eval`` to step forward only the new events.
        self._dfa_consumed: dict[Any, int] = {}

    # -----------------------------------------------------------------
    # State management
    # -----------------------------------------------------------------

    def set_agents(self, agents: dict[str, Any]) -> None:
        """Update the agent map used by grounding for permission lookups."""
        self._agents = agents

    def reset(self) -> None:
        """Drop all cached valuations + accumulators.

        Call this when the trace is reset (e.g. ``RuntimeMonitor.reset``)
        or when the contract set changes in a way that invalidates the
        cached content atoms.

        Trace rollback (``DetBlock`` popping a blocked event) is handled
        automatically by :meth:`sync` — it sees the shrunken length and
        calls this method. You shouldn't need to call it by hand from
        the monitor's hot path.
        """
        self._valuations = []
        self._state.reset()
        self._grounded_upto = 0
        self._last_content_atoms = None
        self._g_cache.clear()
        # DFA backend: rewind every DFA to its initial state. We keep
        # the per-formula instances (compile cost was already paid)
        # but discard their progress on the now-gone trace.
        for dfa in self._dfas.values():
            dfa.reset()
        self._dfa_consumed.clear()

    def sync(
        self,
        trace: Trace,
        content_atoms: dict | None = None,
    ) -> None:
        """Incrementally re-ground the trace.

        Only events strictly after ``self._grounded_upto`` are processed
        — their valuations are appended to ``self._valuations`` and the
        internal :class:`GroundingState` accumulators are updated in
        place. This turns per-check work from O(N) to O(ΔN) where ΔN is
        the number of events added since the last sync (typically 1).

        If the trace has **shrunk** (e.g. the monitor popped the last
        event on a ``DetBlock`` rollback), or if ``content_atoms`` has
        changed relative to the last call, the state is reset and the
        trace is re-grounded from scratch. Both cases are uncommon.

        Args:
            trace: The execution trace to ground.
            content_atoms: Optional dict from
                :func:`sponsio.tracer.grounding.collect_content_atoms`,
                telling grounding which parameterized atoms (``arg_has``,
                ``llm_said``, etc.) to evaluate.
        """
        n = len(trace.events)

        # Roll back if trace shrank or content_atoms changed.
        if n < self._grounded_upto or content_atoms != self._last_content_atoms:
            self.reset()

        self._last_content_atoms = content_atoms

        for i in range(self._grounded_upto, n):
            v = ground_event(
                trace.events[i],
                i,
                self._state,
                content_atoms=content_atoms,
                agents=self._agents,
            )
            self._valuations.append(v)

        self._grounded_upto = n

    def sync_from_contracts(
        self,
        trace: Trace,
        contracts: list[Contract],
    ) -> None:
        """Convenience: collect content atoms from ``contracts`` and sync.

        Most callers that want to verify against a fixed contract set
        should use this — it wires grounding to see exactly the atoms
        those contracts need.
        """
        det_formulas = _collect_det_formulas(contracts)
        content_atoms = collect_content_atoms(det_formulas) or None
        self.sync(trace, content_atoms)

    # -----------------------------------------------------------------
    # Queries (read-only, against currently-synced valuations)
    # -----------------------------------------------------------------

    def check(self, formula: Any) -> Verdict:
        """Evaluate a single formula on the currently-synced trace.

        Accepts either a raw ``Formula`` or a ``DetFormula`` wrapper.
        Does not distinguish assumption vs enforcement — this is the
        generic "does ``formula`` hold now?" query.

        Uses incremental G-cache where possible (see
        :meth:`_incremental_eval`). Falls back to full evaluation for
        formulas whose root is not decomposable.
        """
        raw = _raw_formula(formula)
        holds = self._incremental_eval(raw)
        return Verdict(
            holds=holds,
            desc=_desc(formula),
            formula=formula,
        )

    def check_nl(self, nl: str) -> Verdict:
        """Parse a natural-language rule and check it on the synced trace.

        Convenience for REPL / notebook / quick-script use. Delegates to
        :func:`sponsio.generation.dsl_to_contract.parse_nl_unified` for
        the full "NL string → pattern → raw LTL AST" compile chain, then
        hands the resulting formula to :meth:`check`.

        Only det (formal) NL rules are supported. Soft / sto rules
        (e.g. "response must be empathetic") raise ``ValueError`` —
        those need to go through a :class:`StoEvaluator`, not the
        verifier.

        Example::

            v = TraceVerifier()
            v.sync(my_trace)
            v.check_nl("tool `verify_identity` must precede `transfer_funds`")
            # -> Verdict(holds=True, ...)

        The NL parser is imported lazily so ``TraceVerifier`` itself
        stays independent of ``sponsio.generation`` (which pulls in an
        optional LLM backend). If you never call this method you pay
        no cost.

        Args:
            nl: Natural-language rule string. Must be parseable by
                ``parse_nl_unified`` into a det formula.

        Returns:
            A :class:`Verdict` identical to what :meth:`check` would
            return for the compiled formula.

        Raises:
            ValueError: If the NL string cannot be parsed as a det rule
                (e.g. unsupported pattern, ambiguous, or sto-only text).
        """
        from sponsio.generation.dsl_to_contract import parse_nl_unified

        result = parse_nl_unified(nl)
        if result.is_det:
            return self.check(result.hard)
        if result.is_sto:
            raise ValueError(
                f"check_nl() only supports det (formal) rules; "
                f"{nl!r} parsed as a sto constraint. Sto evaluation is "
                f"not supported in this build (the engine is "
                f"deterministic-only)."
            )
        raise ValueError(
            f"check_nl() could not parse {nl!r} as a det rule. "
            f"Parse error: {result.error or 'unknown'}"
        )

    def _incremental_eval(self, node: Any, finalize: bool = False) -> bool:
        """Dispatch to the selected backend.

        When ``self._backend == "dfa"``, delegates to :meth:`_dfa_eval`
        which runs a per-formula :class:`DFAEvaluator` (formula-
        progression monitor). When ``self._backend == "recursive"``,
        falls back to the G-cached recursive walk.

        ``finalize`` is used by the sto / session-end check path to ask
        "give me the final verdict assuming the trace is complete"
        (``DFAEvaluator.finalize`` — collapses ``"?"`` to ``"⊥"``).
        The recursive backend ignores this flag since it uses weak
        finite-trace semantics uniformly.
        """
        if self._backend == "dfa":
            return self._dfa_eval(node, finalize=finalize)

        # --- Recursive backend (default) ---
        from sponsio.formulas.formula import And, G, Implies, Not, Or

        if isinstance(node, G):
            return self._cached_g_eval(node)
        if isinstance(node, And):
            return self._incremental_eval(node.left) and self._incremental_eval(
                node.right
            )
        if isinstance(node, Or):
            return self._incremental_eval(node.left) or self._incremental_eval(
                node.right
            )
        if isinstance(node, Implies):
            return (not self._incremental_eval(node.left)) or self._incremental_eval(
                node.right
            )
        if isinstance(node, Not):
            return not self._incremental_eval(node.child)

        # Everything else (U, F, X, Atom, Le/Lt/Ge/Gt/Eq, Subset) —
        # use the stateless evaluator, which scans from pos=0.
        return eval_formula(node, self._valuations)

    def _dfa_eval(self, node: Any, finalize: bool = False) -> bool:
        """DFA backend: lazy per-formula :class:`DFAEvaluator`.

        On first call for a given formula, construct a fresh
        ``DFAEvaluator`` and replay any already-grounded events through
        it (catch-up). On subsequent calls, step forward only the new
        events since the last call.

        The ``finalize`` flag asks the DFA to collapse any pending
        ``?`` verdict using weak finite-trace semantics (used by
        session-end checks). Runtime checks leave ``?`` as ``True``
        (i.e., "not yet violated, keep going") to match the current
        monitor's "skip liveness at runtime" behavior.
        """
        from sponsio.formulas.dfa_evaluator import DFAEvaluator

        key = node  # frozen dataclass, hashable — stable across GC
        dfa = self._dfas.get(key)
        if dfa is None:
            dfa = DFAEvaluator(node)
            self._dfas[key] = dfa
            self._dfa_consumed[key] = 0

        # Step the DFA forward for any valuations it hasn't yet seen.
        consumed = self._dfa_consumed.get(key, 0)
        for i in range(consumed, len(self._valuations)):
            dfa.step(self._valuations[i])
        self._dfa_consumed[key] = len(self._valuations)

        if finalize:
            verdict = dfa.finalize()
            return verdict == "⊤"

        # Runtime semantics:
        #   ⊤  → holds
        #   ⊥  → violated
        #   ?  → not yet decided (treat as "not violated yet", don't block)
        verdict = dfa.peek()
        return verdict != "⊥"

    def _cached_g_eval(self, g_node: Any) -> bool:
        """Evaluate ``G(child)`` with incremental caching when safe.

        Cache shape: ``{g_node: (scanned_upto, result)}``. The key is
        the formula object itself (frozen dataclass, hashable) to avoid
        id() collisions when Python GC reuses addresses across calls.
        Only fires when ``child`` is temporally flat (see
        :func:`_is_temporally_flat`) — otherwise falls through to the
        stateless evaluator.

        When cacheable:

        * If previously decided ``False`` → stable false (``G`` is
          monotone-decreasing over growing traces), return cached.
        * If previously ``True`` and the trace hasn't grown, return
          cached.
        * If previously ``True`` and there are new positions, check
          ``child`` only at those new positions, AND with the cached
          result.
        * On first evaluation, do a full scan and memoize.
        """
        n = len(self._valuations)

        # Nested temporal operators invalidate the "old positions stay
        # verified" assumption. Fall back to stateless eval.
        if not _is_temporally_flat(g_node.child):
            return eval_formula(g_node, self._valuations)

        key = g_node  # frozen dataclass, hashable and stable
        cached = self._g_cache.get(key)
        if cached is not None:
            scanned_upto, prev_result = cached
            if not prev_result:
                return False  # stable-false
            if scanned_upto == n:
                return True  # nothing new to check
            child = g_node.child
            for i in range(scanned_upto, n):
                if not eval_formula(child, self._valuations, i):
                    self._g_cache[key] = (n, False)
                    return False
            self._g_cache[key] = (n, True)
            return True

        # First call — full scan, then cache.
        result = eval_formula(g_node, self._valuations)
        self._g_cache[key] = (n, result)
        return result

    def _enforcement_is_fresh(self, raw_formula: Any, eval_pos: int = 0) -> bool:
        """True iff the latest event itself caused the enforcement to fail.

        Used to distinguish a brand-new violation introduced by the
        just-appended event from a *stale* violation already present on
        the prefix. Callers only consult this when they already know the
        enforcement does not hold on the full trace.

        For ``G(child)`` we layer three signals:

        1. **Trace transition.** If the formula held on the prefix
           ``valuations[:-1]`` but not now, the latest event flipped it.
           Always fresh.
        2. **Cumulative-aggregate change.** If ``child`` references any
           ``Var`` aggregate (``count(...)``, ``count_with(...)``,
           ``token_count(...)``, ``consecutive_count(...)``,
           ``delegation_depth``, ``time_since(...)``), fresh iff the
           aggregate's value at ``n-1`` differs from ``n-2``. This is
           what keeps ``rate_limit(Bash, 50)`` from re-blaming an
           unrelated submit-prompt event after the count is already
           over budget — count(Bash) didn't move at that event, so it
           wasn't the cause.
        3. **Event-local predicate fail.** If ``child`` has no
           cumulative aggregate, the formula is purely event-local
           (e.g. ``arg_blacklist`` / ``arg_allowlist`` / ``no_pii`` /
           ``arg_value_range``). Fresh iff ``child`` fails at ``n-1`` —
           i.e. *this* event is itself the violator.

        Cases (1) ∨ ((2) when aggregate present) ∨ ((3) when no aggregate)
        — the layering is deliberate: signal (2) only kicks in when an
        aggregate is present, and signal (3) only when none is present.
        This is what lets ``arg_blacklist`` block back-to-back ``rm``
        calls (each is independently a violator) while ``rate_limit``
        ignores unrelated calls after the limit is blown.

        For non-G shapes we use only signal (1) at the contract's
        evaluation position (``0`` for global, ``k_star`` for reactive).
        """
        from sponsio.formulas.formula import G

        n = len(self._valuations)
        if n == 0:
            return False  # nothing to be fresh-or-stale about
        if n == 1:
            return True  # only one event — it is by definition the cause

        prefix = self._valuations[:-1]

        if isinstance(raw_formula, G):
            # Signal (1): formula held on the prefix and was just flipped.
            if eval_formula(raw_formula, prefix):
                return True

            # Already failed on prefix. Two disjoint sub-cases.
            agg_keys = _collect_cumulative_var_keys(raw_formula.child)
            last_val = self._valuations[-1]
            prior_val = prefix[-1]

            if agg_keys:
                # Signal (2): cumulative-aware. Only fresh if some
                # tracked aggregate moved at this event — otherwise the
                # current event is unrelated to the constrained action
                # and shouldn't be re-blamed.
                for key in agg_keys:
                    if last_val.get(key) != prior_val.get(key):
                        return True
                return False

            # Signal (3): purely event-local predicate. Fresh iff this
            # event itself fails the per-step ``child``.
            return not eval_formula(raw_formula.child, self._valuations, n - 1)

        # Non-G: use the prefix-vs-now signal at the contract's eval
        # position. If the formula already failed on the prefix at the
        # same position, the current event is not the cause.
        if eval_pos >= len(prefix):
            # The current event IS the activation point; treat as fresh.
            return True
        return eval_formula(raw_formula, prefix, eval_pos)

    def check_assumption(self, contract: Contract) -> Verdict:
        """Evaluate a contract's assumption conjunction.

        Short-circuits on the first failing assumption and returns that
        verdict. Sto assumptions are skipped silently (they're handled
        by the sto pipeline, not the verifier).

        Returns a trivially-true verdict (``holds=True``,
        ``desc="true"``) if the contract has no det assumptions or all
        of them pass.
        """
        for a in contract.assumptions:
            if not _is_det(a):
                continue
            raw = _raw_formula(a)
            if not self._incremental_eval(raw):
                return Verdict(
                    holds=False,
                    desc=_desc(a),
                    kind="assumption",
                    formula=a,
                )
        return Verdict(holds=True, desc="true", kind="assumption")

    def check_contract(
        self,
        contract: Contract,
        include_liveness: bool = False,
    ) -> ContractVerdict:
        """Evaluate a whole contract: assumptions, then guarantees.

        Two semantics are supported, switched by ``contract.activate_at``:

        **Default — global semantics** (``activate_at is None``):

        * **Assumptions** are evaluated left-to-right at position 0.
          On the first failure, evaluation stops (short-circuit) and
          the returned ``assumptions`` list ends with that failing
          verdict.
        * **Enforcements** are all evaluated (even if some fail) — but
          only if every assumption held.  Each enforcement is checked
          at position 0 against the *full* trace.

          Suitable for invariants ("if assumption holds throughout,
          then enforcement holds throughout").  See
          ``Contract.activate_at`` docstring.

        **Reactive semantics** (``activate_at == "first_match"``):

        * Each assumption is one of ``F(φ)`` or atomic.  Activation
          point is the first position where φ (resp. the atom) holds.
          The contract activates at the *latest* of all assumptions'
          activation points (since assumptions AND together).
        * If any assumption never activates within the current trace,
          the contract is *vacuously satisfied* — no enforcement
          violations reported.
        * Enforcements are evaluated at position k (the activation
          point), not 0.  Events before k are not subject to E.

          Suitable for trigger-then-enforce safety contracts ("after
          secret read, no outbound POST" should not retroactively flag
          POSTs that happened before the secret read).

        Other notes:

        * Sto constraints (those without a ``formula`` attribute) are
          skipped — they go through the sto pipeline, not here.
        * Liveness formulas (``DetFormula.liveness == True``) are
          skipped by default — at runtime they can't be enforced by
          blocking, so the monitor ignores them mid-session.  Pass
          ``include_liveness=True`` for offline / end-of-session checks.

        Args:
            contract: The contract to evaluate.
            include_liveness: Whether to evaluate liveness guarantees.

        Returns:
            A :class:`ContractVerdict` with per-constraint verdicts.
        """
        if contract.activate_at == "first_match":
            return self._check_contract_reactive(contract, include_liveness)
        return self._check_contract_global(contract, include_liveness)

    def _check_contract_global(
        self,
        contract: Contract,
        include_liveness: bool,
    ) -> ContractVerdict:
        """Default semantics: A and E each evaluated against the full trace."""
        # Assumption phase (short-circuit)
        a_results: list[Verdict] = []
        for a in contract.assumptions:
            if not _is_det(a):
                continue
            raw = _raw_formula(a)
            holds = self._incremental_eval(raw)
            a_results.append(
                Verdict(
                    holds=holds,
                    desc=_desc(a),
                    kind="assumption",
                    formula=a,
                )
            )
            if not holds:
                break

        assumption_holds = all(r.holds for r in a_results)

        # Enforcement phase (only if assumption holds)
        e_results: list[Verdict] = []
        if assumption_holds:
            for e in contract.guarantees:
                if not _is_det(e):
                    continue
                is_liveness = _is_det_formula(e) and getattr(e, "liveness", False)
                if not include_liveness and is_liveness:
                    continue
                raw = _raw_formula(e)
                # Session-end checks on liveness formulas need the DFA
                # to collapse pending "?" to "⊥" (weak finite-trace).
                # The recursive backend already uses weak semantics
                # uniformly, so the ``finalize`` flag is a no-op there.
                holds = self._incremental_eval(
                    raw, finalize=(include_liveness and is_liveness)
                )
                fresh = True if holds else self._enforcement_is_fresh(raw, eval_pos=0)
                e_results.append(
                    Verdict(
                        holds=holds,
                        desc=_desc(e),
                        kind="guarantee",
                        formula=e,
                        fresh=fresh,
                    )
                )

        return ContractVerdict(assumptions=a_results, guarantees=e_results)

    def _check_contract_reactive(
        self,
        contract: Contract,
        include_liveness: bool,
    ) -> ContractVerdict:
        """activate_at='first_match': find activation point, eval E from there.

        Implementation:

        1. For each (det) assumption A, find the first position k_i
           where A's "evidence" first holds (atom or F-child).
        2. If any assumption never activates, return a ContractVerdict
           where that assumption's ``holds=False`` and guarantees is
           empty (vacuous — no violations reported).
        3. Otherwise activation point k = max(k_i) — the latest one to
           fire, since assumptions AND together.
        4. For each enforcement E, evaluate E at pos=k against the
           full valuations.  We bypass the G-cache (which assumes
           pos=0) and call ``eval_formula`` directly with the chosen
           position.
        """
        from sponsio.formulas.evaluator import evaluate as eval_formula
        from sponsio.formulas.formula import Atom, F

        # Find activation point per assumption.  Assumptions whose
        # shape is not F(φ) / atomic shouldn't reach this code path —
        # ``Contract._validate_first_match_assumption_shape`` rejects
        # them at construction time — but we double-check defensively.
        a_results: list[Verdict] = []
        activation_positions: list[int] = []
        n = len(self._valuations)

        for a in contract.assumptions:
            if not _is_det(a):
                continue
            raw = _raw_formula(a)
            if isinstance(raw, F):
                trigger = raw.child
            elif isinstance(raw, Atom):
                trigger = raw
            else:
                # Shouldn't happen — Contract validation should have caught this.
                a_results.append(
                    Verdict(
                        holds=False,
                        desc=_desc(a),
                        kind="assumption",
                        formula=a,
                    )
                )
                break

            # Linear scan for the first position where the trigger holds.
            k = None
            for pos in range(n):
                if eval_formula(trigger, self._valuations, pos):
                    k = pos
                    break

            if k is None:
                # Assumption never activated → contract vacuous.
                a_results.append(
                    Verdict(
                        holds=False,
                        desc=_desc(a),
                        kind="assumption",
                        formula=a,
                    )
                )
                # Short-circuit: no enforcement evaluation.
                return ContractVerdict(assumptions=a_results, guarantees=[])

            activation_positions.append(k)
            a_results.append(
                Verdict(
                    holds=True,
                    desc=_desc(a),
                    kind="assumption",
                    formula=a,
                )
            )

        # All assumptions activated.  Pick the latest activation
        # position so every assumption is satisfied at evaluation time.
        if not activation_positions:
            # Defensive: a contract with activate_at='first_match' but
            # no det assumptions should never have been constructed.
            # Treat as global-default to avoid crashes.
            return self._check_contract_global(contract, include_liveness)
        k_star = max(activation_positions)

        # Enforcement phase at pos=k_star.
        e_results: list[Verdict] = []
        for e in contract.guarantees:
            if not _is_det(e):
                continue
            is_liveness = _is_det_formula(e) and getattr(e, "liveness", False)
            if not include_liveness and is_liveness:
                continue
            raw = _raw_formula(e)
            # Bypass the G-cache (it assumes pos=0).  Call the
            # stateless evaluator directly with our activation
            # position.  Cost: O(suffix_length) per enforcement, which
            # is the right complexity — we explicitly want the suffix
            # semantic.
            holds = eval_formula(raw, self._valuations, k_star)
            fresh = True if holds else self._enforcement_is_fresh(raw, eval_pos=k_star)
            e_results.append(
                Verdict(
                    holds=holds,
                    desc=_desc(e),
                    kind="guarantee",
                    formula=e,
                    fresh=fresh,
                )
            )

        return ContractVerdict(assumptions=a_results, guarantees=e_results)

    # -----------------------------------------------------------------
    # Introspection
    # -----------------------------------------------------------------

    @property
    def valuations(self) -> list[dict]:
        """Current grounded valuations. Read-only view; do not mutate."""
        return self._valuations


# Backward-compatible alias for the original prototype name.
# Prefer ``TraceVerifier`` in new code.
Verifier = TraceVerifier
