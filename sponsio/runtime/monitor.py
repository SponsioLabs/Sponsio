"""RuntimeMonitor. intercepts agent actions and enforces det contracts.

This is the central enforcement point.  Every agent action flows through
``check_action()``, which runs the deterministic evaluation pipeline:

    action -> append to trace -> ground(trace) -> for each Contract:
        eval assumption -> if holds, eval enforcement
    -> pass:  action allowed
    -> fail:  DetBlock | EscalateToHuman | WarnOnly

Each ``Contract`` is a single (assumption, enforcement) pair. Contracts
are evaluated independently: an assumption on one contract never gates
the enforcement of another contract.

This build is det-only. The LLM-judged stochastic pipeline (sto
evaluator + retry / redirect strategies + feedback generator + the
sto-specific dispatch this module used to host) is an extension point
not part of this build. ``BaseGuard`` keeps a public
``sto_evaluator: StoEvaluator | None`` hook (typed against
:mod:`sponsio.protocols.sto`) and delegates to it through the
``check_soft`` / ``refine`` surface; this monitor stays oblivious.
"""

from __future__ import annotations

import logging
import weakref
from dataclasses import dataclass
from typing import Any, Callable

from sponsio.models.result import Violation
from sponsio.models.spans import AgentTurnSpan, SpanCollector
from sponsio.models.system import System
from sponsio.models.trace import Event, Trace
from sponsio.runtime.evaluators import DetEvaluator
from sponsio.runtime.perf import CheckTimer, PerformanceTracker
from sponsio.runtime.strategies import (
    ActionContext,
    DetBlock,
    EnforcementResult,
    EnforcementStrategy,
    EscalateToHuman,
)
from sponsio.runtime.verifier import TraceVerifier, Verdict


logger = logging.getLogger(__name__)


@dataclass
class MonitorEvent:
    """Record of a runtime monitor check.

    Attributes:
        agent_id: Agent that triggered the check.
        action: Action/tool being checked.
        constraint_name: Name of the violated constraint.
        result: The enforcement result.
        pipeline: Which pipeline produced the event: always ``"det"``
            from this monitor. An injected ``StoEvaluator`` populates
            ``"sto"`` on the events it emits through ``BaseGuard``.
            Kept on the dataclass so dashboards / session-loggers /
            OTel exporters can group events without sniffing the
            ``StoResult`` payload.
        sto_result: Sidecar populated on sto violations (score,
            evidence, suggestion). ``None`` for everything this
            monitor emits.
    """

    agent_id: str
    action: str
    constraint_name: str
    result: EnforcementResult
    pipeline: str = "det"
    sto_result: Any = None


class RuntimeMonitor:
    """Runtime enforcement monitor for multi-agent systems.

    Intercepts agent actions, evaluates them against det contracts,
    and applies per-constraint enforcement strategies. Sto contracts
    (LLM-judged atoms) are an extension point that this monitor does
    not handle directly; ``BaseGuard`` routes those through its own
    ``StoEvaluator`` hook.

    Thread safety: ``check_action``, ``reset``, ``import_trace``, and
    the ``log`` / ``turn_spans`` snapshot accessors are serialised by
    an internal :class:`~threading.RLock`. This is the configuration
    that matters for the FastAPI demo server (``api/state.py``,
    thread-pool sync routes) and the MCP proxy
    (``sponsio.integrations.mcp``, one proxy shared across concurrent
    tool clients). Callbacks fire *outside* the lock so a slow exporter
    (dashboard HTTP, OTel) can't stall the agent loop. Contract
    authoring APIs on the underlying ``System`` (e.g.
    ``system._contracts.append``) are **not** guarded; treat contracts
    as write-once at startup.

    Args:
        system: The System whose contracts are being enforced.
        policy: Mapping of constraint descriptions to enforcement strategies.
        mode: Enforcement mode. ``"enforce"`` (default) runs strategies
            normally. det violations block. ``"observe"`` (shadow mode)
            evaluates every contract but downgrades all violations to
            ``"observed"`` so nothing is blocked; callbacks still fire,
            so a ``SessionLogger`` hooked into the monitor captures the
            full record of what *would* have happened under real
            enforcement.
        hard_evaluator: Deprecated. Previously accepted a ``DetEvaluator``
            for custom hard predicates; the value was stored but never
            consulted by any code path, so users who passed it got no
            enforcement on their custom predicates. Kept as a kwarg for
            source compatibility. emits ``DeprecationWarning`` when
            non-None and is otherwise ignored. Custom predicates today
            should be expressed as pattern factories in
            ``sponsio.patterns.library``.
    """

    def __init__(
        self,
        system: System,
        hard_evaluator: DetEvaluator | None = None,
        policy: dict[str, EnforcementStrategy] | None = None,
        mode: str = "enforce",
    ) -> None:
        if mode not in ("enforce", "observe"):
            raise ValueError(f"mode must be 'enforce' or 'observe', got {mode!r}")
        if hard_evaluator is not None:
            # The previous implementation stored this on ``self`` and
            # never read it. operators believed they had wired custom
            # det predicates through the monitor when in fact nothing
            # checked them, silently disabling their intended contracts.
            # Fail loudly instead of silently accepting.
            import warnings

            warnings.warn(
                "RuntimeMonitor(hard_evaluator=...) is deprecated and has "
                "no effect: the argument was never consulted by the "
                "evaluation pipeline. Express custom hard predicates via "
                "pattern factories in sponsio.patterns.library instead. "
                "This kwarg will be removed in a future release.",
                DeprecationWarning,
                stacklevel=2,
            )
        self._system = system
        self._policy = policy or {}
        self._mode = mode
        # Persistent per-contract memo of sto atom evaluations, keyed by
        # ``(id(atom), position)`` inside each contract's sub-dict. Event
        # content at a given position is immutable once appended, so a
        # deterministic (T=0) judge call for the same atom at the same
        # position always gives the same answer. Caching this drops the
        # cost of re-evaluating G/F/U formulas on every new event from
        # O(n) to O(1) LLM calls per new event. total linear instead of
        # quadratic over a session.
        #
        # WeakKeyDictionary keyed by the Contract object itself: we used
        # to key on ``id(contract)`` but Python GC can reuse an int id
        # after the original Contract is collected, leading to two
        # unrelated contracts sharing a cache. Contract is declared with
        # ``eq=False`` precisely so it stays hashable by identity here.
        self._atom_caches: weakref.WeakKeyDictionary[
            Any, dict[tuple[int, int], float]
        ] = weakref.WeakKeyDictionary()
        import threading

        # Reentrant: ``check_action`` takes the lock for the full pipeline
        # (trace mutation, verifier sync, sto eval, span collection) and
        # internally calls ``_emit`` which also takes this lock. Using an
        # RLock lets ``_emit`` re-enter without deadlocking the hot path.
        # The concurrency guarantee: at most one thread runs a
        # ``check_action`` / ``reset`` / ``import_trace`` at a time on a
        # given monitor, so ``trace.events`` ordering, ``_verifier`` sync
        # state, and ``_atom_caches`` can't interleave. Callbacks fire
        # outside the lock (same as before) so a slow exporter doesn't
        # back-pressure the agent loop.
        self._lock = threading.RLock()
        self._log: list[MonitorEvent] = []
        self._trace = Trace(events=[])
        self._callbacks: list[Callable[[MonitorEvent], None]] = []
        self._last_turn_span: AgentTurnSpan | None = None
        self._turn_spans: list[AgentTurnSpan] = []
        self._verifier = TraceVerifier()
        # Per-check timing.  Always-on. cost is a ``perf_counter_ns``
        # call (≈20ns on modern CPUs) plus a deque.append, both of
        # which are dominated by the actual contract evaluation.
        # Users who want it disabled can still access a summary with
        # n=0. no code path branches on "tracker is None".
        self._perf_tracker = PerformanceTracker()
        # Dry-run depth: > 0 means we're inside a probe (e.g.
        # ``filter_tools``) and must suppress every side-effect
        # surface: MonitorEvent log writes, turn-span appends, perf
        # samples, observe-mode downgrades. A counter (not a bool)
        # tolerates nested probes: if a registered callback re-enters
        # ``check_action(dry_run=True)``, the inner probe increments
        # to 2, decrements back to 1 on exit, and side effects only
        # resume when the outermost frame drops to 0.
        self._dry_run_depth = 0

    @property
    def mode(self) -> str:
        """Enforcement mode: ``"enforce"`` or ``"observe"`` (shadow)."""
        return self._mode

    def _maybe_downgrade(self, result: EnforcementResult) -> EnforcementResult:
        """In observe mode, downgrade any enforcement action to ``"observed"``.

        The original action is preserved in the message so reporters and
        JSONL sessions can see what *would* have happened.

        During a dry-run probe (``_dry_run_depth > 0``) the downgrade is
        bypassed so callers like ``filter_tools`` see the raw
        ``"blocked"`` outcome regardless of mode. Conceptually
        "would this tool be legal?" is mode-independent: in observe mode
        the constraint still detects a violation, it just chooses not to
        enforce it.
        """
        if self._dry_run_depth > 0:
            return result
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
            score=result.score,
            threshold=result.threshold,
            rule_id=result.rule_id,
            agent_msg=result.agent_msg,
            retry_hint=result.retry_hint,
            alternatives=list(result.alternatives),
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
        # Dry-run probes (``filter_tools``) suppress every observable
        # side effect: no log entry, no callbacks, no OTEL push, no
        # session-log write. The probe still mutates the verifier's
        # atom cache, but ``rollback_last_event`` clears that on the
        # way out. The depth check runs *inside* the lock so a future
        # caller invoking ``_emit`` from a callback / OTEL hook that
        # doesn't already hold the lock still sees a coherent value.
        with self._lock:
            if self._dry_run_depth > 0:
                return
            self._log.append(event)
            callbacks = list(self._callbacks)
        for fn in callbacks:
            fn(event)

    @property
    def trace(self) -> Trace:
        return self._trace

    def import_trace(self, trace: Trace) -> None:
        """Replace the current trace and invalidate derived verifier state.

        Thread-safe: a concurrent ``check_action`` must not see a trace
        that's been replaced while the verifier's ``_grounded_upto`` is
        still pointing at the old trace's length. ``sync`` would try
        to ``ground_event`` for a nonexistent index.
        """
        with self._lock:
            self._trace = trace
            self._verifier.reset()
            self._last_turn_span = None
            self._turn_spans.clear()
            self._atom_caches.clear()

    def rollback_last_event(self) -> bool:
        """Pop the last trace event and invalidate all derived caches.

        Used by ``BaseGuard.guard_before`` when a det violation blocks
        the action that was just appended. the trace must look as if
        it never happened so subsequent checks aren't poisoned.

        Clears three things together (this is the load-bearing part):

        * ``trace.events.pop()``. undo the append.
        * ``verifier.reset()``. drop grounded valuations + per-formula
          DFA progress + G-cache; next ``sync`` re-grounds from scratch.
        * ``_atom_caches.clear()``. sto atom scores are keyed by
          ``(id(atom), position)``; the popped position is about to be
          reused by the next event, so a stale cache at that position
          would surface yesterday's score on tomorrow's content.

        Thread-safe under ``self._lock``. Returns True if an event was
        popped, False if the trace was already empty.
        """
        with self._lock:
            if not self._trace.events:
                return False
            self._trace.events.pop()
            self._verifier.reset()
            self._atom_caches.clear()
            return True

    @property
    def performance_tracker(self) -> PerformanceTracker:
        """The :class:`PerformanceTracker` recording per-check latencies.

        Always present (never ``None``) so consumers can always call
        ``monitor.performance_tracker.summarize()`` without a
        guard-clause. an un-used monitor just returns an empty
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
        # Snapshot under the lock. the list is being appended to by
        # any concurrent ``check_action`` and reading mid-append can
        # intermittently return an inconsistent Python list state.
        with self._lock:
            return list(self._turn_spans)

    def render_last_turn(self, colorize: bool = True) -> str:
        if self._last_turn_span is None:
            return ""
        from sponsio.models.spans import render_tree

        return render_tree(self._last_turn_span, colorize=colorize)

    def reset(self) -> None:
        """Resets the monitor state (trace, log, spans, verifier cache).

        Thread-safe: holds ``self._lock`` for the duration so a
        concurrent ``check_action`` on another thread doesn't observe a
        half-cleared state (e.g. trace empty but ``_atom_caches`` still
        pointing at the old positions, which would have the next event
        reuse stale cached atom scores).
        """
        with self._lock:
            self._trace = Trace(events=[])
            self._log.clear()
            self._last_turn_span = None
            self._turn_spans.clear()
            self._verifier.reset()
            # Clear the per-contract atom memo. entries are keyed by
            # (id(atom), position) and positions are about to be reused.
            self._atom_caches.clear()
        # Intentionally DO NOT reset the perf tracker.  Perf is a
        # session-scoped aggregate; a user resetting the trace to
        # re-run doesn't want to lose the speed evidence.  If they do
        # want a clean slate they can call ``performance_tracker.reset()``
        # explicitly.

    def rotate_session(self) -> dict:
        """Begin a new session window; return a summary of what was flushed.

        This is the **supported** way to bound memory in long-running
        agents (24/7 service agents, always-on schedulers) without
        losing contract enforcement. It behaves exactly like
        :meth:`reset`. trace, log, spans, verifier cache, and atom
        caches are all cleared; contracts on the underlying
        :class:`~sponsio.models.system.System` are **not** touched.
        The only difference is intent signalling and the return value:
        callers get back the headline metrics of the window that just
        closed so they can plumb them into audit logs / dashboards
        before the numbers go away.

        Why not just keep using :meth:`reset`?
        ``reset`` reads as "something went wrong, start over".
        ``rotate_session`` is the name you want to see at a quarterly
        review. "we rotate every 1000 turns to cap memory; here's the
        hand-off record."

        Liveness caveat
        ---------------
        Formulas that span the **entire trace**. ``F(tool)`` /
        ``always_followed_by(a, b)`` / whole-trace ``rate_limit(tool, N)``
       . lose visibility across the rotation boundary. Concretely: if
        ``response`` was promised before ``rotate_session`` and still
        hasn't happened, the post-rotation verifier won't see the
        original ``trigger`` and can never fire the liveness violation.
        To avoid silently eating obligations, this method refuses to
        rotate while ``finish_session`` hasn't been called on a guard
        with pending liveness obligations. but since ``RuntimeMonitor``
        doesn't know about guard-level ``finish_session``, the check
        has to happen one layer up. See
        :meth:`sponsio.integrations.base.BaseGuard.rotate_session` for
        the guard-side handling: run ``finish_session`` first, then
        rotate.

        Returns
        -------
        dict
            ``{"events": int, "turns": int, "log_entries": int,
            "violations_cleared": 0}`` (``violations_cleared`` is always
            0 at the monitor layer. violations are tracked by
            :class:`~sponsio.integrations.base.BaseGuard`, not here).
        """
        with self._lock:
            summary = {
                "events": len(self._trace.events),
                "turns": len(self._turn_spans),
                "log_entries": len(self._log),
                "violations_cleared": 0,  # BaseGuard populates this
            }
            # Emit an INFO-level log record so ops can correlate
            # rotations with dashboard / memory metrics. No-op if the
            # user hasn't wired logging. stdlib default is WARNING.
            logger.info(
                "RuntimeMonitor.rotate_session: events=%d turns=%d log=%d",
                summary["events"],
                summary["turns"],
                summary["log_entries"],
            )
            # Delegate to reset() for the actual clearing. reset() also
            # takes _lock but it's an RLock, so this re-entry is fine
            # and guarantees rotate_session sees the exact state it
            # reported.
            self.reset()
        return summary

    def check_action(
        self,
        agent_id: str,
        action: str,
        event_type: str = "tool_call",
        metadata: dict | None = None,
        dry_run: bool = False,
    ) -> list[EnforcementResult]:
        """Checks a proposed agent action against all applicable contracts.

        Thread-safe: the full pipeline. event construction + trace
        append + det evaluation + sto evaluation + span collection.
        runs under ``self._lock`` (an RLock). This is the only way to
        get a consistent ``ts`` ordering when multiple threads call
        ``check_action`` on the same monitor (FastAPI sync routes in
        ``api/state.py``, MCP proxy serving concurrent clients). Pre-fix
        two threads could race on ``trace.events.append`` and
        ``verifier.sync``: one writes ts=5, the other writes ts=5 too,
        the verifier double-processes the same slot, atom caches
        interleave, and the resulting trace is silently inconsistent.

        Args:
            dry_run: When True, the call runs as a side-effect-free
                probe. MonitorEvent log writes, callback fanout,
                turn-span recording, perf samples, and observe-mode
                downgrades are all suppressed. Returns the raw
                enforcement results so callers like ``filter_tools``
                can decide whether the action *would* be blocked. The
                caller is still responsible for ``rollback_last_event``
                to undo the appended trace event.
        """
        with self._lock:
            if dry_run:
                self._dry_run_depth += 1
            try:
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

                    collector.root.total_contracts_checked = sum(
                        1
                        for c in collector.root.children
                        if c.span_type == "sponsio.contract_check"
                    )
                    collector.root.det_violations = len(hard_results)
                    collector.root.blocked = any(r.action == "blocked" for r in results)
                    if results:
                        collector.root.status = "violated"

                if not dry_run:
                    self._last_turn_span = collector.root
                    self._turn_spans.append(collector.root)

                return results
            finally:
                if dry_run:
                    self._dry_run_depth -= 1

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

        Delegates all formal evaluation to ``self._verifier``. this
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
            e_count = len(contract.guarantees)
            label = contract.desc or f"{contract.agent.id}: {a_count}A/{e_count}E"

            # OSS is det-only. Non-det contracts (sto atoms, legacy
            # StoFormula) are filtered out earlier in ``BaseGuard``.
            # if one slips through here we silently skip rather than
            # crash the agent loop.
            if not contract.is_pure_det:
                continue
            collector.start_contract_check(label, pipeline="det")
            # ``is_pure_det=True`` is the guarantee we can hand to the
            # CheckTimer: this branch mathematically cannot make an LLM
            # call, so the sample will end up in the ``pure_det`` bucket
            # no matter what. During a dry-run probe (``filter_tools``)
            # we pass ``None`` so the probe doesn't pollute perf stats
            # with synthetic 20+ samples per model turn.
            timer_tracker = None if self._dry_run_depth > 0 else self._perf_tracker
            with CheckTimer(timer_tracker, label, is_pure_det=True):
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
            for e_verdict in verdict.guarantees:
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

                # Stale violation guard: the enforcement is False on the
                # full trace, but the latest event isn't the cause. the
                # rule was already broken on the prefix. Don't blame
                # (and block) the current event for a historical
                # violation. This matters most under observe-mode where
                # nothing rolls back the offending prior event, but it's
                # the right semantic everywhere: an action should only
                # be denied for what *it* did.
                if not e_verdict.fresh:
                    collector.finish_span("ok")
                    self._emit_pass_event(
                        agent_id=agent_id,
                        action=context.action,
                        constraint_name=e_verdict.desc,
                        pass_desc=(
                            f"PASSED (stale prior violation, not caused by "
                            f"this event): {e_verdict.desc}"
                        ),
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
        strategy = self._policy.get(a_verdict.lookup_key)
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

        strategy = self._policy.get(e_verdict.lookup_key)
        if strategy is None:
            # Patterns can attach a strategy at construction (e.g.
            # ``redirect_to_safe`` bundles ``RedirectToSafe``); honour
            # that before falling back to the default block. Explicit
            # ``policy={...}`` from the user above still wins.
            strategy = getattr(e_verdict.formula, "enforcement_strategy", None)
        if strategy is None:
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
            constraint_name=e_verdict.desc,
            result=enf_result,
        )
        self._emit(monitor_event)
        return enf_result
