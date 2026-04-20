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
from typing import Callable

from sponsio.models.result import Violation
from sponsio.models.spans import AgentTurnSpan, SpanCollector
from sponsio.models.system import System
from sponsio.models.trace import Event, Trace
from sponsio.runtime.evaluators import DetEvaluator, StoEvaluator, StoResult
from sponsio.runtime.feedback import FeedbackGenerator
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
    ) -> None:
        if mode not in ("enforce", "observe"):
            raise ValueError(f"mode must be 'enforce' or 'observe', got {mode!r}")
        self._system = system
        self._hard_evaluator = hard_evaluator
        self._sto_evaluator = sto_evaluator
        self._policy = policy or {}
        self._mode = mode
        self._feedback_generator = FeedbackGenerator()
        import threading

        self._lock = threading.Lock()
        self._log: list[MonitorEvent] = []
        self._trace = Trace(events=[])
        self._callbacks: list[Callable[[MonitorEvent], None]] = []
        self._last_turn_span: AgentTurnSpan | None = None
        self._turn_spans: list[AgentTurnSpan] = []
        self._verifier = TraceVerifier()

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
        for strategy in self._policy.values():
            if isinstance(strategy, RetryWithConstraint):
                strategy.reset()

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
            collector.start_contract_check(label, pipeline="det")

            verdict = self._verifier.check_contract(contract)

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
