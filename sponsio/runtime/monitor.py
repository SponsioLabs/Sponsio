"""RuntimeMonitor -- intercepts agent actions and enforces contracts at runtime.

This is the central enforcement point.  Every agent action flows through
``check_action()``, which runs two independent evaluation pipelines:

Det pipeline (formal, binary):
    action -> append to trace -> ground(trace) -> for each Contract:
        eval assumption -> if holds, eval enforcement
    -> pass:  action allowed
    -> fail:  DetBlock or EscalateToHuman

Sto pipeline (probabilistic, graded):
    action -> for each sto-enforcement Contract whose assumption holds:
        StoEvaluator -> StoResult(score, evidence, suggestion)
    -> score >= threshold:  pass
    -> score <  threshold:  RetryWithConstraint or RedirectToSafe

Det violations NEVER use sto strategies (and vice versa).  This is
enforced in ``_check_det`` and ``_check_sto``.

Each ``Contract`` is a single (assumption, enforcement) pair. Contracts
are evaluated independently — an assumption on one contract never gates
the enforcement of another contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from sponsio.models.result import Violation
from sponsio.models.spans import AgentTurnSpan, SpanCollector
from sponsio.models.system import System
from sponsio.models.trace import Event, Trace
from sponsio.runtime.evaluators import DetEvaluator, StoEvaluator, StoResult
from sponsio.runtime.feedback import FeedbackGenerator
from sponsio.runtime.perf import CheckTimer, PerformanceTracker
from sponsio.runtime.strategies import (
    ActionContext,
    EnforcementResult,
    EnforcementStrategy,
    DetBlock,
    EscalateToHuman,
    RetryWithConstraint,
    RedirectToSafe,
)
from sponsio.runtime.verifier import TraceVerifier, Verdict, _is_det


@dataclass
class MonitorEvent:
    """Record of a runtime monitor check.

    Attributes:
        agent_id: Agent that triggered the check.
        action: Action/tool being checked.
        pipeline: Which pipeline flagged it ("det" or "sto").
        constraint_name: Name of the violated constraint.
        result: The enforcement result.
        sto_result: StoResult if from the sto pipeline.
    """

    agent_id: str
    action: str
    pipeline: str  # "det" or "sto"
    constraint_name: str
    result: EnforcementResult
    sto_result: StoResult | None = None


class LessonFormatter:
    """Renders a contract-aware retry lesson from a failed sto verdict.

    The lesson is the discriminative signal the model needs on retry:
    which contract was violated, the measured confidence, the threshold
    it missed, and any evaluator-supplied evidence / suggestion. Kept
    as a class (vs a function) so each integration can subclass it to
    render in the framework's native form — OpenAI system message,
    LangGraph checkpoint-inject, CrewAI memory note — if plain text
    isn't the right channel.
    """

    @staticmethod
    def build(contract, verdict) -> str:
        """Produce a plain-text lesson string for a ``retry_prompt`` field.

        Args:
            contract: The ``Contract`` whose enforcement was violated.
            verdict: The sto ``Verdict`` with ``score`` and ``threshold``
                populated.

        Returns:
            Multi-line plain text suitable for prepending to the next
            user turn or injecting as a system message.
        """
        pieces: list[str] = []
        label = contract.desc or verdict.desc
        pieces.append(f"[Contract reminder: {label}]")
        pieces.append("Your previous attempt did not meet this requirement.")
        if verdict.score is not None and verdict.threshold is not None:
            pieces.append(
                f"Confidence score: {verdict.score:.2f} "
                f"(needs ≥ {verdict.threshold:.2f})."
            )
        if verdict.evidence:
            pieces.append(f"Evidence: {verdict.evidence}")
        if verdict.suggestion:
            pieces.append(f"Suggestion: {verdict.suggestion}")
        pieces.append("Please revise and retry.")
        return "\n".join(pieces)


def _has_liftable_formulas(contract) -> bool:
    """True iff every non-empty constraint in the contract wraps (or is)
    a ``Formula`` AST — meaning it can be evaluated via
    ``eval_sto_confidence``. Legacy :class:`StoFormula` (closure-based
    ``evaluator_fn``) returns False; those are handled by
    :meth:`_check_sto`.
    """
    from sponsio.formulas.formula import FormulaMixin

    items = contract.assumptions + contract.enforcements
    if not items:
        return False
    for item in items:
        if isinstance(item, FormulaMixin):
            continue
        inner = getattr(item, "formula", None)
        if not isinstance(inner, FormulaMixin):
            return False
    return True


class RuntimeMonitor:
    """Runtime enforcement monitor for multi-agent systems.

    Intercepts agent actions, evaluates them against contracts using
    dual pipelines (det/sto), and applies per-constraint enforcement
    strategies.

    Args:
        system: The System whose contracts are being enforced.
        hard_evaluator: Optional DetEvaluator for custom hard predicates.
        sto_evaluator: Optional StoEvaluator for sto constraints.
        policy: Mapping of constraint descriptions to enforcement strategies.
        mode: Enforcement mode. ``"enforce"`` (default) runs strategies
            normally — det violations block, sto violations retry.
            ``"observe"`` (shadow mode) evaluates every contract but
            downgrades all violations to ``"observed"`` so nothing is
            blocked; callbacks still fire, so a ``SessionLogger`` hooked
            into the monitor captures the full record of what *would* have
            happened under real enforcement.
    """

    def __init__(
        self,
        system: System,
        hard_evaluator: DetEvaluator | None = None,
        sto_evaluator: StoEvaluator | None = None,
        policy: dict[str, EnforcementStrategy] | None = None,
        mode: str = "enforce",
        sto_judge: Any = None,
    ) -> None:
        if mode not in ("enforce", "observe"):
            raise ValueError(f"mode must be 'enforce' or 'observe', got {mode!r}")
        self._system = system
        self._hard_evaluator = hard_evaluator
        self._sto_evaluator = sto_evaluator
        self._policy = policy or {}
        self._mode = mode
        # Per-monitor sto judge. None means "fall back to module-level
        # set_default_judge(), or fail if neither is configured". See
        # sponsio.patterns.sto_catalog._require_judge.
        self._sto_judge = sto_judge
        # Persistent per-contract memo of sto atom evaluations, keyed by
        # (id(atom), position). Event content at a given position is
        # immutable once appended, so a deterministic (T=0) judge call
        # for the same atom at the same position always gives the same
        # answer. Caching this drops the cost of re-evaluating G/F/U
        # formulas on every new event from O(n) to O(1) LLM calls per
        # new event — total linear instead of quadratic over a session.
        self._atom_caches: dict[int, dict[tuple[int, int], float]] = {}
        self._feedback_generator = FeedbackGenerator()
        import threading

        self._lock = threading.Lock()
        self._log: list[MonitorEvent] = []
        self._trace = Trace(events=[])
        self._callbacks: list[Callable[[MonitorEvent], None]] = []
        self._last_turn_span: AgentTurnSpan | None = None
        self._turn_spans: list[AgentTurnSpan] = []
        self._verifier = TraceVerifier()
        # Per-check timing.  Always-on — cost is a ``perf_counter_ns``
        # call (≈20ns on modern CPUs) plus a deque.append, both of
        # which are dominated by the actual contract evaluation.
        # Users who want it disabled can still access a summary with
        # n=0 — no code path branches on "tracker is None".
        self._perf_tracker = PerformanceTracker()

    @property
    def mode(self) -> str:
        """Enforcement mode: ``"enforce"`` or ``"observe"`` (shadow)."""
        return self._mode

    def _maybe_downgrade(self, result: EnforcementResult) -> EnforcementResult:
        """In observe mode, downgrade any enforcement action to ``"observed"``.

        The original action is preserved in the message so reporters and
        JSONL sessions can see what *would* have happened.
        """
        if self._mode != "observe":
            return result
        original = result.action
        # Keep the original action literal intact for anyone sniffing the
        # message; prepend OBSERVED so downstream filters on
        # ``action=="blocked"`` stop firing.
        new_msg = f"OBSERVED (would {original}): {result.message}"
        return EnforcementResult(
            action="observed",  # type: ignore[arg-type]
            message=new_msg,
            retry_prompt=result.retry_prompt,
            fallback_action=result.fallback_action,
        )

    @property
    def verifier(self) -> TraceVerifier:
        """The underlying :class:`TraceVerifier` used for formal evaluation.

        Exposed for callers that want to run ad-hoc verification queries
        without going through the enforcement pipeline (no spans, no
        strategies, no trace mutation).
        """
        return self._verifier

    def register_callback(self, fn: Callable[[MonitorEvent], None]) -> None:
        """Register a callback to be invoked on every monitor event."""
        with self._lock:
            self._callbacks.append(fn)

    def _emit(self, event: MonitorEvent) -> None:
        with self._lock:
            self._log.append(event)
            callbacks = list(self._callbacks)
        for fn in callbacks:
            fn(event)

    @property
    def trace(self) -> Trace:
        return self._trace

    def import_trace(self, trace: Trace) -> None:
        """Replace the current trace and invalidate derived verifier state."""
        self._trace = trace
        self._verifier.reset()
        self._last_turn_span = None
        self._turn_spans.clear()
        self._atom_caches.clear()

    @property
    def performance_tracker(self) -> PerformanceTracker:
        """The :class:`PerformanceTracker` recording per-check latencies.

        Always present (never ``None``) so consumers can always call
        ``monitor.performance_tracker.summarize()`` without a
        guard-clause — an un-used monitor just returns an empty
        summary.
        """
        return self._perf_tracker

    @property
    def log(self) -> list[MonitorEvent]:
        with self._lock:
            return list(self._log)

    @property
    def last_turn_span(self) -> AgentTurnSpan | None:
        return self._last_turn_span

    @property
    def turn_spans(self) -> list[AgentTurnSpan]:
        return list(self._turn_spans)

    def render_last_turn(self, colorize: bool = True) -> str:
        if self._last_turn_span is None:
            return ""
        from sponsio.models.spans import render_tree

        return render_tree(self._last_turn_span, colorize=colorize)

    def reset(self) -> None:
        """Resets the monitor state (trace, log, spans, verifier cache)."""
        self._trace = Trace(events=[])
        self._log.clear()
        self._last_turn_span = None
        self._turn_spans.clear()
        self._verifier.reset()
        # Clear the per-contract atom memo — entries are keyed by
        # (id(atom), position) and positions are about to be reused.
        self._atom_caches.clear()
        for strategy in self._policy.values():
            if isinstance(strategy, RetryWithConstraint):
                strategy.reset()
        # Intentionally DO NOT reset the perf tracker.  Perf is a
        # session-scoped aggregate; a user resetting the trace to
        # re-run doesn't want to lose the speed evidence.  If they do
        # want a clean slate they can call ``performance_tracker.reset()``
        # explicitly.

    def check_action(
        self,
        agent_id: str,
        action: str,
        event_type: str = "tool_call",
        metadata: dict | None = None,
    ) -> list[EnforcementResult]:
        """Checks a proposed agent action against all applicable contracts."""
        meta = metadata or {}

        event = Event(
            ts=len(self._trace.events),
            agent=agent_id,
            event_type=event_type,
            tool=action if event_type == "tool_call" else None,
            key=meta.get("key"),
            contains=meta.get("contains"),
            to=meta.get("to"),
            args=meta.get("args"),
            content=meta.get("content"),
        )
        self._trace.events.append(event)

        context = ActionContext(
            agent_id=agent_id,
            action=action,
            trace_length=len(self._trace.events),
            metadata=meta,
        )

        results: list[EnforcementResult] = []

        with SpanCollector(agent_id, action) as collector:
            hard_results = self._check_det(agent_id, context, collector)
            results.extend(hard_results)

            sto_results = self._check_sto(agent_id, context, collector)
            results.extend(sto_results)

            collector.root.total_contracts_checked = sum(
                1
                for c in collector.root.children
                if c.span_type == "sponsio.contract_check"
            )
            for child in collector.root.children:
                if child.span_type == "sponsio.sto_check":
                    collector.root.total_contracts_checked += sum(
                        1 for sc in child.children if sc.span_type == "sponsio.sto_eval"
                    )
            collector.root.det_violations = len(hard_results)
            collector.root.sto_violations = len(sto_results)
            collector.root.blocked = any(r.action == "blocked" for r in results)
            if results:
                collector.root.status = "violated"

        self._last_turn_span = collector.root
        self._turn_spans.append(collector.root)

        return results

    # -----------------------------------------------------------------
    # Det pipeline
    # -----------------------------------------------------------------

    def _check_det(
        self,
        agent_id: str,
        context: ActionContext,
        collector: SpanCollector,
    ) -> list[EnforcementResult]:
        """Runs the hard evaluation pipeline.

        Delegates all formal evaluation to ``self._verifier`` — this
        method only walks the returned verdicts, emits spans, and
        applies enforcement strategies. Contracts are independent:
        a failed assumption on one does not gate another.
        """
        results: list[EnforcementResult] = []

        # Sync the verifier with the current trace + contract set.
        agents = {c.agent.id: c.agent for c in self._system.contracts}
        self._verifier.set_agents(agents)
        self._verifier.sync_from_contracts(self._trace, self._system.contracts)

        for contract in self._system.contracts:
            if contract.agent.id != agent_id:
                continue

            a_count = len(contract.assumptions)
            e_count = len(contract.enforcements)
            label = contract.desc or f"{contract.agent.id}: {a_count}A/{e_count}E"

            # Dispatch:
            # - pure-det contracts go through the existing LTL evaluator
            # - contracts whose formulas are Formula ASTs (possibly
            #   containing sto atoms) take the new probabilistic-lifting
            #   path with α / β
            # - legacy StoFormula (closure-based evaluator_fn) are NOT
            #   our business — they're handled by _check_sto later
            if contract.is_pure_det:
                collector.start_contract_check(label, pipeline="det")
                # ``is_pure_det=True`` is the guarantee we can hand
                # to the CheckTimer: this branch mathematically
                # cannot make an LLM call, so the sample will end
                # up in the ``pure_det`` bucket no matter what.
                with CheckTimer(self._perf_tracker, label, is_pure_det=True):
                    verdict = self._verifier.check_contract(contract)
            elif _has_liftable_formulas(contract):
                collector.start_contract_check(label, pipeline="sto")
                # Scope the per-guard judge to the evaluation so
                # atom-registered evaluators pick it up via ContextVar.
                from sponsio.patterns.sto_catalog import _use_judge

                # ``is_pure_det=False`` defers bucket selection until
                # the timer exits — if the TLS LLM-call counter moved
                # during evaluation the sample goes to ``sto_live``,
                # otherwise to ``sto_cached`` (meaning: this sto
                # contract was resolved purely from the atom memo,
                # zero LLM calls on this turn — which is the common
                # steady-state path we want to make visible).
                with CheckTimer(self._perf_tracker, label, is_pure_det=False):
                    if self._sto_judge is not None:
                        with _use_judge(self._sto_judge):
                            verdict = self._check_contract_with_confidence(contract)
                    else:
                        verdict = self._check_contract_with_confidence(contract)
            else:
                # Legacy StoFormula contract — _check_sto handles it.
                continue

            # --- Assumption phase ---
            assumption_violated = False
            for a_verdict in verdict.assumptions:
                pre_span = collector.start_precondition(a_verdict.desc)

                if a_verdict.holds:
                    collector.finish_span("ok")
                    self._emit_pass_event(
                        agent_id=agent_id,
                        action=context.action,
                        constraint_name=f"assumption: {a_verdict.desc}",
                        pass_desc=f"PASSED: assumption {a_verdict.desc}",
                    )
                    continue

                pre_span.result = False
                collector.finish_span("violated")
                results.append(
                    self._handle_assumption_failure(
                        agent_id=agent_id,
                        context=context,
                        collector=collector,
                        a_verdict=a_verdict,
                    )
                )
                assumption_violated = True
                break

            if assumption_violated:
                collector.finish_span("violated")  # close contract_check
                continue

            # --- Enforcement phase ---
            contract_violated = False
            for e_verdict in verdict.enforcements:
                guar_span = collector.start_guarantee(e_verdict.desc)

                if e_verdict.holds:
                    collector.finish_span("ok")
                    self._emit_pass_event(
                        agent_id=agent_id,
                        action=context.action,
                        constraint_name=e_verdict.desc,
                        pass_desc=f"PASSED: {e_verdict.desc}",
                    )
                    continue

                guar_span.result = False
                collector.finish_span("violated")
                if e_verdict.is_sto:
                    # Sto violation: retry with confidence-aware lesson,
                    # not a hard block. Matches the probabilistic
                    # semantics of β — the model can plausibly fix its
                    # output on retry.
                    results.append(
                        self._handle_sto_enforcement_failure(
                            agent_id=agent_id,
                            context=context,
                            collector=collector,
                            e_verdict=e_verdict,
                            contract=contract,
                        )
                    )
                else:
                    results.append(
                        self._handle_enforcement_failure(
                            agent_id=agent_id,
                            context=context,
                            collector=collector,
                            e_verdict=e_verdict,
                        )
                    )
                contract_violated = True

            collector.finish_span("violated" if contract_violated else "ok")

        return results

    def _check_contract_with_confidence(self, contract):
        """Probabilistic-lifting evaluation for contracts with sto atoms
        or non-default (α, β) thresholds.

        Returns a :class:`ContractVerdict`-shaped result using synthetic
        :class:`Verdict` objects so the downstream span / enforcement
        handling in :meth:`_check_det` works unchanged.

        Semantics (see ``docs/cost-based-thresholds.md`` §2):

        * Assumption triggered iff ``conf(A) ≥ contract.alpha``. When
          *not* triggered, every enforcement is marked ``holds=True``
          (vacuously satisfied).
        * Each enforcement entry is satisfied iff ``conf(E) ≥ contract.beta``.
        * Lists on either side are treated as independent entries —
          each gets its own synthetic Verdict. This mirrors the det
          path and keeps per-entry span output.
        """
        from sponsio.runtime.sto_lifting import eval_sto_confidence
        from sponsio.runtime.verifier import ContractVerdict, Verdict
        from sponsio.tracer.grounding import collect_content_atoms, ground

        def _unwrap(item):
            inner = getattr(item, "formula", None)
            return inner if inner is not None else item

        def _describe(item, fallback: str) -> str:
            desc = getattr(item, "desc", None)
            if desc:
                return desc
            # Fall back to repr (Atom → "injection_free()", Not → "!(...)")
            # before the generic fallback string — much more useful for
            # logs and retry prompts than "enforcement".
            if item is not None:
                try:
                    return repr(item)
                except Exception:
                    pass
            return fallback

        formulas = [_unwrap(x) for x in contract.assumptions + contract.enforcements]
        content_atoms = collect_content_atoms(formulas)
        agents = {c.agent.id: c.agent for c in self._system.contracts}
        valuations = ground(self._trace, agents=agents, content_atoms=content_atoms)

        cache: dict = {}
        # Persistent per-contract atom cache. Event content at each
        # position is immutable once appended, so an atom's score at
        # that position never changes after the first evaluation.
        # Reusing the cache across check_action calls drops the cost
        # of formulas like G(atom) from O(n) LLM calls per event to
        # O(1) — total linear instead of quadratic.
        atom_cache = self._atom_caches.setdefault(id(contract), {})
        cv = ContractVerdict()

        # --- Assumption side ---
        # IMPORTANT — the sto assumption is a *trigger threshold*, not a
        # precondition. conf(A) < α means "the contract doesn't apply
        # right now", which is semantically different from a det
        # assumption violation (upstream flow problem → escalate).
        # When not triggered we return an empty ContractVerdict so the
        # monitor skips the contract cleanly, no escalation emitted.
        triggered = True
        for a_item in contract.assumptions:
            formula = _unwrap(a_item)
            try:
                conf = eval_sto_confidence(
                    formula,
                    valuations,
                    self._trace,
                    t=0,
                    cache=cache,
                    atom_cache=atom_cache,
                )
            except Exception as e:
                # Missing sto evaluator / bad formula — surface as a
                # real assumption failure so it escalates to a human.
                cv.assumptions.append(
                    Verdict(
                        holds=False,
                        desc=f"{_describe(a_item, 'assumption')} [lifting error: {e}]",
                        kind="assumption",
                        formula=formula,
                    )
                )
                return cv  # abort — no enforcement side
            if conf < contract.alpha:
                triggered = False
                break  # contract doesn't apply; skip enforcement entirely

        # --- Enforcement side ---
        if not triggered:
            # Not triggered → vacuously satisfied. Empty ContractVerdict
            # tells the monitor "nothing to check for this contract".
            return cv

        for e_item in contract.enforcements:
            formula = _unwrap(e_item)
            try:
                conf = eval_sto_confidence(
                    formula,
                    valuations,
                    self._trace,
                    t=0,
                    cache=cache,
                    atom_cache=atom_cache,
                )
            except Exception as e:
                cv.enforcements.append(
                    Verdict(
                        holds=False,
                        desc=f"{_describe(e_item, 'enforcement')} [lifting error: {e}]",
                        kind="enforcement",
                        formula=formula,
                    )
                )
                continue
            holds = conf >= contract.beta
            label = (
                f"{_describe(e_item, 'enforcement')} "
                f"[conf={conf:.3f}, β={contract.beta:.3f}]"
            )
            cv.enforcements.append(
                Verdict(
                    holds=holds,
                    desc=label,
                    kind="enforcement",
                    formula=formula,
                    score=float(conf),
                    threshold=float(contract.beta),
                )
            )

        return cv

    # -----------------------------------------------------------------
    # Det-pipeline helpers (side effects isolated from eval)
    # -----------------------------------------------------------------

    def _emit_pass_event(
        self,
        agent_id: str,
        action: str,
        constraint_name: str,
        pass_desc: str,
    ) -> None:
        """Emit a pass-through ``MonitorEvent`` so reporters see successes."""
        self._emit(
            MonitorEvent(
                agent_id=agent_id,
                action=action,
                pipeline="det",
                constraint_name=constraint_name,
                result=EnforcementResult(action="allowed", message=pass_desc),
            )
        )

    def _handle_assumption_failure(
        self,
        agent_id: str,
        context: ActionContext,
        collector: SpanCollector,
        a_verdict: Verdict,
    ) -> EnforcementResult:
        """Convert a failed assumption verdict into a Violation + strategy result."""
        violation = Violation(
            agent_id=agent_id,
            formula=a_verdict.formula,
            kind="assumption",
            desc=a_verdict.desc,
            details=(
                f"Assumption violated: {a_verdict.desc}. "
                "The upstream agent flow may have a problem."
            ),
        )
        strategy = self._policy.get(a_verdict.desc)
        if strategy is None:
            strategy = EscalateToHuman()

        collector.add_violation(
            kind="assumption",
            severity="HIGH",
            evidence=violation.details,
        )
        collector.add_enforcement(
            strategy=type(strategy).__name__,
            result_action="escalated",
        )

        enforcement_result = self._maybe_downgrade(strategy.enforce(violation, context))
        monitor_event = MonitorEvent(
            agent_id=agent_id,
            action=context.action,
            pipeline="det",
            constraint_name=f"assumption: {a_verdict.desc}",
            result=enforcement_result,
        )
        self._emit(monitor_event)
        return enforcement_result

    def _handle_enforcement_failure(
        self,
        agent_id: str,
        context: ActionContext,
        collector: SpanCollector,
        e_verdict: Verdict,
    ) -> EnforcementResult:
        """Convert a failed enforcement verdict into a Violation + strategy result."""
        violation = Violation(
            agent_id=agent_id,
            formula=e_verdict.formula,
            kind="guarantee",
            desc=e_verdict.desc,
            details=f"Runtime det violation: {e_verdict.desc}",
        )

        strategy = self._policy.get(e_verdict.desc)
        if strategy is None:
            strategy = DetBlock()
        # Validate: det violations must use hard strategies
        if isinstance(strategy, (RetryWithConstraint, RedirectToSafe)):
            strategy = DetBlock()

        enf_result = self._maybe_downgrade(strategy.enforce(violation, context))

        collector.add_violation(
            kind="guarantee",
            severity="HIGH",
            evidence=violation.details,
        )
        collector.add_enforcement(
            strategy=type(strategy).__name__,
            result_action=enf_result.action,
        )

        monitor_event = MonitorEvent(
            agent_id=agent_id,
            action=context.action,
            pipeline="det",
            constraint_name=e_verdict.desc,
            result=enf_result,
        )
        self._emit(monitor_event)
        return enf_result

    def _handle_sto_enforcement_failure(
        self,
        agent_id: str,
        context: ActionContext,
        collector: SpanCollector,
        e_verdict: Verdict,
        contract,
    ) -> EnforcementResult:
        """Route a stochastic enforcement violation through RetryWithConstraint
        with a confidence-aware lesson.

        Unlike the det path (which uses ``DetBlock``), sto violations
        give the model a chance to fix its output. The lesson explains
        what the judge measured and by how much the response fell short.
        """
        violation = Violation(
            agent_id=agent_id,
            formula=e_verdict.formula,
            kind="guarantee",
            desc=e_verdict.desc,
            details=(
                f"Sto violation: {e_verdict.desc}. "
                f"Confidence {e_verdict.score:.3f} fell short of β={e_verdict.threshold:.3f}."
            ),
        )

        # Honor any user-configured strategy override; else default to
        # RetryWithConstraint. Reject det strategies here — they would
        # drop the retry prompt.
        strategy = self._policy.get(e_verdict.desc)
        if strategy is None or isinstance(strategy, (DetBlock, EscalateToHuman)):
            strategy = RetryWithConstraint(max_retries=2)

        # Build the discriminative lesson.
        lesson = LessonFormatter.build(
            contract=contract,
            verdict=e_verdict,
        )

        enf_result = strategy.enforce(violation, context)
        # Overwrite the strategy's bland retry_prompt with our confidence-
        # aware version, and attach score/threshold for reporters.
        enf_result = EnforcementResult(
            action=enf_result.action,
            message=enf_result.message,
            retry_prompt=lesson,
            fallback_action=enf_result.fallback_action,
            score=e_verdict.score,
            threshold=e_verdict.threshold,
        )
        enf_result = self._maybe_downgrade(enf_result)

        collector.add_violation(
            kind="guarantee",
            severity="MEDIUM",
            evidence=violation.details,
        )
        collector.add_enforcement(
            strategy=type(strategy).__name__,
            result_action=enf_result.action,
        )

        self._emit(
            MonitorEvent(
                agent_id=agent_id,
                action=context.action,
                pipeline="sto",
                constraint_name=e_verdict.desc,
                result=enf_result,
                sto_result=StoResult(
                    score=e_verdict.score if e_verdict.score is not None else 0.0,
                    evidence=e_verdict.evidence or violation.details,
                    suggestion=e_verdict.suggestion or "",
                ),
            )
        )
        return enf_result

    # -----------------------------------------------------------------
    # Sto pipeline
    # -----------------------------------------------------------------

    def _check_sto(
        self,
        agent_id: str,
        context: ActionContext,
        collector: SpanCollector,
    ) -> list[EnforcementResult]:
        """Runs the sto evaluation pipeline.

        For each contract whose enforcement contains sto constraints:
        1. Evaluate the contract's assumption (det). If it fails, skip
           all sto constraints on this contract.
        2. Otherwise, run each sto constraint through the evaluator and
           apply retry/redirect strategies for any below-threshold
           scores.
        """
        results: list[EnforcementResult] = []

        if self._sto_evaluator is None:
            return results

        # Sync verifier so assumption-gating queries hit current trace state.
        # _check_det ran first and already synced, but resync defensively
        # in case something mutated the trace between the two pipelines.
        agents = {c.agent.id: c.agent for c in self._system.contracts}
        self._verifier.set_agents(agents)
        self._verifier.sync_from_contracts(self._trace, self._system.contracts)

        # Collect prop names owned by each contract on this agent
        owned_by_contract: set[str] = set()
        gated_pass: set[str] = set()  # props whose contract's assumption holds
        gated_fail: set[str] = set()  # props whose contract's assumption fails

        for contract in self._system.contracts:
            if contract.agent.id != agent_id:
                continue
            for e in contract.enforcements:
                if _is_det(e):
                    continue
                prop_name = getattr(e, "desc", str(e))
                owned_by_contract.add(prop_name)
                if contract.is_unconditional:
                    gated_pass.add(prop_name)
                else:
                    a_verdict = self._verifier.check_assumption(contract)
                    if a_verdict.holds:
                        gated_pass.add(prop_name)
                    else:
                        gated_fail.add(prop_name)

        # Active = any prop that either (a) is attached to a contract whose
        # assumption currently holds, or (b) is registered on the evaluator
        # directly without any owning contract (treat as unconditional).
        checked = self._sto_evaluator.check(self._trace)
        if not checked:
            return results

        active: set[str] = set()
        for prop_name in checked:
            if prop_name in gated_fail and prop_name not in gated_pass:
                continue  # gated out by failing assumption
            if prop_name in gated_pass:
                active.add(prop_name)
            elif prop_name not in owned_by_contract:
                # Not attached to any contract for this agent -> unconditional
                active.add(prop_name)

        if not active:
            return results

        collector.start_sto_check()

        for prop_name, (passed, sto_result) in checked.items():
            if prop_name not in active:
                continue  # assumption gating filtered this out

            threshold = self._sto_evaluator.get_threshold(prop_name)
            collector.start_sto_eval(
                constraint_name=prop_name,
                score=sto_result.score,
                threshold=threshold,
                passed=passed,
            )

            if passed:
                collector.finish_span("ok")
                continue

            collector.add_violation(
                kind="sto",
                severity="MEDIUM",
                evidence=sto_result.evidence,
            )

            violation = Violation(
                agent_id=agent_id,
                formula=None,  # type: ignore[arg-type]
                kind="sto",
                desc=prop_name,
                details=f"Sto constraint violated: {sto_result.evidence}",
            )

            strategy = self._policy.get(prop_name)
            if strategy is None:
                strategy = RetryWithConstraint(
                    max_retries=2, feedback_generator=self._feedback_generator
                )
            if isinstance(strategy, (DetBlock, EscalateToHuman)):
                strategy = RetryWithConstraint(
                    max_retries=2, feedback_generator=self._feedback_generator
                )

            if isinstance(strategy, RetryWithConstraint):
                template = self._sto_evaluator.get_feedback_template(prop_name)
                enf_result = strategy.enforce(
                    violation,
                    context,
                    sto_result=sto_result,
                    feedback_template=template,
                )
            else:
                enf_result = strategy.enforce(violation, context)

            enf_result = self._maybe_downgrade(enf_result)

            collector.add_enforcement(
                strategy=type(strategy).__name__,
                result_action=enf_result.action,
            )

            collector.finish_span("violated")

            monitor_event = MonitorEvent(
                agent_id=agent_id,
                action=context.action,
                pipeline="sto",
                constraint_name=prop_name,
                result=enf_result,
                sto_result=sto_result,
            )
            self._emit(monitor_event)
            results.append(enf_result)

        collector.finish_span("violated" if results else "ok")

        return results
