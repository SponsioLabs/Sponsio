"""BaseGuard — unified parent class for all framework integrations.

Every framework adapter (LangGraph, MCP, CrewAI, etc.) inherits from
BaseGuard. The base class owns all contract logic:

    NL parsing → System/Monitor setup → guard_before → guard_after → refine

Subclasses only implement the framework-specific interception mechanism
(callback, proxy, wrapper, etc.).

Dual pipeline:
    Det constraints → guard_before() → block / escalate (before tool runs)
    Sto constraints → guard_after()  → refine / redirect (after tool runs)
"""

from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sponsio.generation.nl_to_contract import parse_nl_unified
from sponsio.models.agent import Agent
from sponsio.models.contract import Contract
from sponsio.models.spans import AgentTurnSpan, render_tree
from sponsio.models.system import System
from sponsio.models.trace import Trace
from sponsio.runtime.evaluators import StoEvaluator, StoResult
from sponsio.runtime.feedback import FeedbackGenerator
from sponsio.runtime.monitor import RuntimeMonitor
from sponsio.runtime.session_log import SessionLogger
from sponsio.runtime.strategies import (
    EnforcementResult,
    EnforcementStrategy,
    DetBlock,
    WarnOnly,
)


_VALID_MODES = ("enforce", "observe")


def _resolve_mode(mode: str | None) -> str:
    """Resolve the effective mode from explicit arg + ``SPONSIO_MODE`` env.

    The env var is an escape hatch that lets users flip modes without
    editing code — crucial for staged rollouts and CI smoke-tests.
    Precedence: env var wins over the explicit argument so
    ``SPONSIO_MODE=observe`` lets ops force shadow mode in production
    without a code change.
    """
    env = os.environ.get("SPONSIO_MODE")
    resolved = env.strip() if env else (mode or "enforce")
    if resolved not in _VALID_MODES:
        raise ValueError(f"mode must be one of {_VALID_MODES}, got {resolved!r}")
    return resolved


# ---------------------------------------------------------------------------
# Check result (returned by guard_before / guard_after)
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    """Outcome of a pre- or post-check.

    Attributes:
        allowed: Whether the action is allowed to proceed.
        det_violations: Det constraint violations (block/escalate).
        sto_violations: Sto constraint violations (retry/redirect).
        feedback: Discriminative feedback prompt for sto retry.
            Inject this into the agent's next prompt to guide regeneration.
        rollback_performed: Whether the trace was rolled back (hard block).
    """

    allowed: bool = True
    det_violations: list[EnforcementResult] = field(default_factory=list)
    sto_violations: list[EnforcementResult] = field(default_factory=list)
    feedback: str | None = None
    rollback_performed: bool = False

    @property
    def blocked(self) -> bool:
        """True if any det violation resulted in a block."""
        return any(r.action == "blocked" for r in self.det_violations)

    @property
    def needs_retry(self) -> bool:
        """True if any sto violation returned a retry with feedback."""
        return any(r.action == "retrying" for r in self.sto_violations)

    @property
    def all_violations(self) -> list[EnforcementResult]:
        return self.det_violations + self.sto_violations


# ---------------------------------------------------------------------------
# BaseGuard
# ---------------------------------------------------------------------------


class BaseGuard:
    """Base class for all framework integrations.

    Owns the full contract lifecycle:
        1. Parse NL contracts → LTL formulas
        2. Build System + RuntimeMonitor
        3. guard_before()  — det constraints, before tool execution
        4. guard_after()   — sto constraints, after tool execution
        5. refine()     — generate feedback for sto retry
        6. Trace management (rollback on block, reset between sessions)

    Subclasses override the framework-specific interception point
    (e.g. on_tool_start for LangGraph, call_tool for MCP).

    Args:
        agent_id: Logical agent identifier for trace/monitor.
        contracts: List of contract entries. Each entry is one of:

            - **Dict** — ``{"assumption": <scalar|list|None>, "enforcement": <scalar|list>}``.
              ``assumption`` is optional (``None`` = unconditional). Lists
              are AND-combined. Becomes one :class:`Contract`.
            - **NL string** — shortcut for an unconditional contract
              (``assumption=None``, ``enforcement=<string>``).
            - **Pre-built** :class:`Contract` — passed through as-is.

            Each entry becomes one independent ``Contract`` whose
            enforcement is gated only on its own assumption — assumptions
            never cross contracts.
        system: Pre-built System (alternative to the above).
        policy: Per-constraint enforcement strategy overrides.
            Keys are constraint descriptions, values are strategy instances.
            Defaults: det → DetBlock, sto → RetryWithConstraint.
        sto_evaluator: Optional StoEvaluator for sto constraints.
        store: Optional PatternStore. If provided, user-written NL
            contracts are automatically registered as ``user_defined``.
    """

    def __init__(
        self,
        agent_id: str = "agent",
        contracts: list[dict | Contract | str] | None = None,
        config: str | None = None,
        system: System | None = None,
        policy: dict[str, EnforcementStrategy] | None = None,
        sto_evaluator: StoEvaluator | None = None,
        store: Any | None = None,
        dashboard_url: str | None = None,
        otel_exporter: Any | None = None,
        verbose: bool = True,
        verbosity: int = 1,
        mode: str | None = None,
        session_log_dir: str | Path | None = None,
    ) -> None:
        # --- Config file support ---
        if config is not None:
            if contracts is not None:
                raise ValueError(
                    "Cannot combine 'config' with 'contracts'. "
                    "Use either a config file or inline contracts, not both."
                )
            from sponsio.config import config_to_guard_kwargs, load_config

            parsed = load_config(config)
            # Auto-infer agent_id
            if agent_id == "agent" and agent_id not in parsed.agents:
                if len(parsed.agents) == 1:
                    agent_id = next(iter(parsed.agents))
                elif len(parsed.agents) > 1:
                    available = list(parsed.agents.keys())
                    raise ValueError(
                        f"Config has multiple agents {available}. "
                        f"Please specify agent_id=... explicitly."
                    )
            cfg = config_to_guard_kwargs(parsed, agent_id)
            contracts = cfg.get("contracts")
            if system is None:
                system = cfg.get("system")

        self.agent_id = agent_id
        self._mode = _resolve_mode(mode)
        self._session_log_dir = (
            Path(session_log_dir) if session_log_dir is not None else None
        )
        self._session_logger: SessionLogger | None = None
        self._violations: list[dict] = []
        self._violation_actions: dict[str, str] = {}
        self._lock = threading.Lock()
        # Session-end liveness check state: idempotency flag + cache of
        # the last computed pending-liveness verdicts. Updated by
        # :meth:`finish_session`.
        self._finish_session_called: bool = False
        self._pending_liveness_violations: list = []
        self._store = store
        self._dashboard_url = self._validate_dashboard_url(dashboard_url)
        self._otel = otel_exporter
        self._verbose = verbose
        self._verbosity = verbosity

        # --- Build system from contracts ---
        self._system = system if system is not None else System(name="guarded")

        user_formulas: list = []
        soft_constraints: list = []

        agent_model = Agent(id=agent_id)
        built_contracts = self._build_contracts(
            agent_model=agent_model,
            contracts=contracts,
            user_formulas=user_formulas,
            soft_constraints=soft_constraints,
        )
        for c in built_contracts:
            self._system._contracts.append(c)

        # Auto-register sto constraints on the StoEvaluator
        if soft_constraints:
            if sto_evaluator is None:
                sto_evaluator = StoEvaluator()
            for sc in soft_constraints:
                sto_evaluator.register(
                    prop_name=sc.desc,
                    fn=sc.evaluator_fn,
                    threshold=sc.threshold,
                    feedback_template=sc.feedback_template,
                )

        # Register user-defined contracts in the store
        if self._store is not None and user_formulas:
            self._store.import_user_defined(user_formulas)

        # --- Build default policy: hard block for all enforcements ---
        if policy is not None:
            self._policy = policy
        else:
            self._policy = {}
            for contract in self._system._contracts:
                for e in contract.enforcements:
                    if not hasattr(e, "desc"):
                        continue
                    action = self._violation_actions.get(e.desc, "block")
                    if action in ("warn", "log"):
                        self._policy[e.desc] = WarnOnly()
                    else:
                        self._policy[e.desc] = DetBlock()

        # --- Create monitor ---
        self._monitor = RuntimeMonitor(
            system=self._system,
            sto_evaluator=sto_evaluator,
            policy=self._policy,
            mode=self._mode,
        )

        # --- Terminal reporter ---
        if self._verbose:
            from sponsio.runtime.terminal import TerminalReporter

            self._monitor.register_callback(
                TerminalReporter(
                    verbosity=self._verbosity,
                    contracts=list(self._system._contracts),
                )
            )

        # --- Shadow-mode session logger ---
        # Always attach the JSONL logger in observe mode so users have a
        # durable record of what would-have-happened. In enforce mode we
        # skip it by default to avoid surprise writes to $HOME; the dir
        # override is honored either way for users who want full logging.
        if self._mode == "observe" or self._session_log_dir is not None:
            try:
                self._session_logger = SessionLogger(
                    agent_id=self.agent_id,
                    base_dir=self._session_log_dir,
                )
                self._monitor.register_callback(self._session_logger)
            except Exception as exc:
                # Logging must never break the agent — surface a hint
                # to stderr and continue.
                print(
                    f"[sponsio] session logger disabled: {exc}",
                    file=sys.stderr,
                )

    # -----------------------------------------------------------------
    # Contract construction
    # -----------------------------------------------------------------

    def _build_contracts(
        self,
        agent_model: Agent,
        contracts: list[dict | Contract | str] | None,
        user_formulas: list,
        soft_constraints: list,
    ) -> list[Contract]:
        """Normalize the ``contracts`` kwarg into a list of ``Contract`` objects.

        Each entry becomes one :class:`Contract`. List-valued
        ``assumption``/``enforcement`` fields have each element parsed
        independently, preserving the list (the monitor ANDs them at
        check time).
        """
        out: list[Contract] = []

        for entry in contracts or []:
            if isinstance(entry, Contract):
                out.append(entry)
                for e in entry.enforcements:
                    self._register_constraint(e, user_formulas, soft_constraints)
                continue

            if isinstance(entry, str):
                # Bare string = unconditional contract shorthand
                parsed = self._parse_constraint(entry, user_formulas, soft_constraints)
                if parsed is None:
                    continue
                out.append(Contract(agent=agent_model, enforcement=parsed))
                continue

            if not isinstance(entry, dict):
                raise TypeError(
                    f"contracts[] entries must be dict, Contract, or str; "
                    f"got {type(entry).__name__}"
                )

            # Reject YAML-style short keys in Python to keep the split clean.
            if "A" in entry or "E" in entry:
                raise ValueError(
                    f"Python contract dicts must use full keys "
                    f"'assumption'/'enforcement'. Short keys 'A'/'E' are "
                    f"YAML-only. Got: {entry!r}"
                )

            e_raw = entry.get("enforcement")
            if e_raw is None:
                raise ValueError(f"Contract entry missing 'enforcement': {entry!r}")
            a_raw = entry.get("assumption")
            desc = entry.get("desc")

            parsed_e = self._parse_constraint_field(
                e_raw, user_formulas, soft_constraints
            )
            parsed_a = (
                None
                if a_raw is None
                else self._parse_constraint_field(
                    a_raw, user_formulas, soft_constraints
                )
            )

            out.append(
                Contract(
                    agent=agent_model,
                    enforcement=parsed_e,
                    assumption=parsed_a,
                    desc=desc,
                )
            )

        return out

    def _parse_constraint_field(
        self,
        value: Any,
        user_formulas: list,
        soft_constraints: list,
    ) -> Any:
        """Parse a single scalar or list field (assumption / enforcement)."""
        if isinstance(value, list):
            return [
                self._parse_constraint(v, user_formulas, soft_constraints)
                for v in value
                if self._parse_constraint(v, user_formulas, soft_constraints)
                is not None
            ]
        return self._parse_constraint(value, user_formulas, soft_constraints)

    def _parse_constraint(
        self,
        value: Any,
        user_formulas: list,
        soft_constraints: list,
    ) -> Any:
        """Parse a single constraint: NL string, DetFormula, or StoFormula."""
        if isinstance(value, str):
            result = parse_nl_unified(value)
            if result.is_det:
                user_formulas.append(result.hard)
                return result.hard
            if result.is_sto:
                soft_constraints.append(result.sto)
                return result.sto
            return None
        # Pre-compiled formula object
        self._register_constraint(value, user_formulas, soft_constraints)
        return value

    def _register_constraint(
        self,
        value: Any,
        user_formulas: list,
        soft_constraints: list,
    ) -> None:
        if hasattr(value, "formula"):
            user_formulas.append(value)
        elif hasattr(value, "evaluator_fn"):
            soft_constraints.append(value)

    # -----------------------------------------------------------------
    # Dashboard streaming
    # -----------------------------------------------------------------

    @staticmethod
    def _validate_dashboard_url(url: str | None) -> str | None:
        """Validate dashboard URL to prevent SSRF attacks."""
        if url is None:
            return None
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                f"dashboard_url must use http:// or https:// scheme, got {parsed.scheme!r}"
            )
        if not parsed.hostname:
            raise ValueError("dashboard_url must have a hostname")
        # Block common internal targets
        blocked = {"metadata.google.internal", "169.254.169.254"}
        if parsed.hostname in blocked:
            raise ValueError(f"dashboard_url hostname {parsed.hostname!r} is blocked")
        return url

    def _push_to_dashboard(
        self, event_type: str, tool: str | None = None, content: str | None = None
    ) -> None:
        """Fire-and-forget push to dashboard. Uses urllib (stdlib, zero deps)."""
        if not self._dashboard_url:
            return
        try:
            import json
            import urllib.request

            data = json.dumps(
                {
                    "agent": self.agent_id,
                    "type": event_type,
                    "tool": tool,
                    "content": content,
                }
            ).encode()
            req = urllib.request.Request(
                f"{self._dashboard_url}/api/monitor/push",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=2)
        except Exception as exc:
            print(
                f"[sponsio] dashboard push failed: {exc}",
                file=sys.stderr,
            )

    def _otel_export(self) -> None:
        """Export the last span tree to OTEL if an exporter is configured."""
        if self._otel is None:
            return
        span = self.last_check_span
        if span is not None:
            try:
                self._otel.export(span)
            except Exception as exc:
                print(
                    f"[sponsio] OTEL export failed: {exc}",
                    file=sys.stderr,
                )

    def export_trace(self) -> dict:
        """Export trace for POST /monitor/import."""
        trace_data = self.trace.to_dict()
        return {
            "events": trace_data["events"],
            "metadata": {
                **(trace_data.get("metadata") or {}),
                "violations": self._violations,
                "agent_id": self.agent_id,
            },
        }

    # -----------------------------------------------------------------
    # Core check methods (framework-agnostic)
    # -----------------------------------------------------------------

    def guard_before(self, tool_name: str, args: dict | None = None) -> CheckResult:
        """Check contracts BEFORE tool execution.

        Runs the det pipeline. If a det violation is detected, the
        event is rolled back from the trace (as if it never happened)
        so subsequent checks aren't poisoned.

        Args:
            tool_name: Name of the tool being called.
            args: Tool arguments (for metadata/logging).

        Returns:
            CheckResult with allowed=False if blocked.
        """
        with self._lock:
            metadata = {"args": args} if args else {}

            results = self._monitor.check_action(
                agent_id=self.agent_id,
                action=tool_name,
                metadata=metadata,
            )

            hard = [r for r in results if r.action in ("blocked", "escalated")]
            warned = [r for r in results if r.action == "warned"]
            observed = [r for r in results if r.action == "observed"]
            sto_list = [r for r in results if r.action in ("retrying", "redirected")]

            result = CheckResult(
                allowed=not any(r.action == "blocked" for r in hard),
                det_violations=hard + warned + observed,
                sto_violations=sto_list,
            )

            # Rollback blocked events from trace (NOT for warned/observed).
            # Observe mode never rolls back — the whole point is to show
            # users the trace their agent would have produced.
            if (
                self._mode != "observe"
                and result.blocked
                and self._monitor.trace.events
            ):
                self._monitor.trace.events.pop()
                # Tell the verifier its cache is stale. Next sync() will
                # see trace length == grounded_upto - 1 and re-ground.
                self._monitor.verifier.reset()
                result.rollback_performed = True

            # Collect feedback from sto retries
            retry_prompts = [r.retry_prompt for r in sto_list if r.retry_prompt]
            if retry_prompts:
                result.feedback = "\n".join(retry_prompts)

            # Record violations
            for r in result.all_violations:
                self._violations.append(
                    {
                        "tool": tool_name,
                        "constraint": r.message,
                        "action": r.action.upper(),
                    }
                )

        self._push_to_dashboard("tool_call", tool=tool_name)
        self._otel_export()
        return result

    def guard_after(self, tool_name: str, output: Any) -> CheckResult:
        """Check sto constraints AFTER tool execution.

        Use this for output-quality constraints (tone, PII, format)
        that can only be evaluated once the tool has produced output.

        The sto evaluator runs on the current trace. If violations
        are found, discriminative feedback is generated for retry.

        Args:
            tool_name: Name of the tool that just ran.
            output: The tool's output (for context in feedback).

        Returns:
            CheckResult with feedback if sto violations detected.
        """
        with self._lock:
            if self._monitor._sto_evaluator is None:
                return CheckResult(allowed=True)

            checked = self._monitor._sto_evaluator.check(self._monitor.trace)
            sto_violations: list[EnforcementResult] = []
            feedback_parts: list[str] = []

            feedback_gen = FeedbackGenerator()

            for prop_name, (passed, sto_result) in checked.items():
                if passed:
                    continue

                # Generate feedback
                template = self._monitor._sto_evaluator.get_feedback_template(prop_name)
                fb = feedback_gen.generate(prop_name, sto_result, template)
                feedback_parts.append(fb)

                sto_violations.append(
                    EnforcementResult(
                        action="retrying",
                        message=f"SOFT: {prop_name} \u2014 {sto_result.evidence}",
                        retry_prompt=fb,
                    )
                )

                self._violations.append(
                    {
                        "tool": tool_name,
                        "constraint": f"sto: {prop_name}",
                        "action": "RETRY",
                        "score": sto_result.score,
                        "feedback": fb,
                    }
                )

        summary = (
            "; ".join(f"{r.message}" for r in sto_violations)
            if sto_violations
            else "all passed"
        )
        self._push_to_dashboard("soft_check", tool=tool_name, content=summary)
        self._otel_export()
        return CheckResult(
            allowed=len(sto_violations) == 0,
            sto_violations=sto_violations,
            feedback="\n".join(feedback_parts) if feedback_parts else None,
        )

    def refine(
        self, constraint_name: str, sto_result: StoResult, template: str | None = None
    ) -> str:
        """Generate discriminative feedback for a sto constraint violation.

        This is the feedback string you inject into the agent's next
        prompt to guide it toward compliant output on retry.

        Priority: explicit template > registered template > generic fallback.

        Args:
            constraint_name: Name of the violated constraint.
            sto_result: The StoResult from evaluation.
            template: Optional template override.

        Returns:
            Formatted feedback string for agent re-prompting.
        """
        gen = FeedbackGenerator()
        return gen.generate(constraint_name, sto_result, template)

    # Backward-compatible aliases
    pre_check = guard_before
    post_check = guard_after

    def wrap(self, tools: list) -> list:
        """Wrap tools with contract enforcement.

        Framework-specific subclasses override this to return the
        appropriate wrapped type (e.g. LangGraph ``ToolNode``, CrewAI
        ``Tool`` list). The base implementation returns tools unchanged
        — use ``guard_before()`` / ``guard_after()`` manually.

        Args:
            tools: List of tool objects or callables.

        Returns:
            Tools (possibly wrapped) with contract enforcement.
        """
        return tools

    # Backward-compatible alias
    def tools(self, *args, **kwargs):
        """Deprecated: use ``wrap()`` instead."""
        return self.wrap(*args, **kwargs)

    # -----------------------------------------------------------------
    # Observation hooks — inject non-tool-call events into the trace
    # -----------------------------------------------------------------
    # These methods extend the observable surface beyond tool calls,
    # enabling atoms like llm_said, prompt_contains, output_has,
    # token_count, flow, contains, and delegation_depth.
    #
    # Integration adapters (LangGraph, MCP, etc.) should call these
    # from their framework-specific hooks. They do NOT run enforcement
    # (no blocking / no strategies) — they just enrich the trace so
    # subsequent guard_before checks have richer grounding data.

    def observe_llm_call(
        self,
        prompt: str | None = None,
        response: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        """Record an LLM request/response pair in the trace.

        Enables atoms: ``prompt_contains``, ``llm_said``,
        ``token_count``, ``system_prompt_present``, ``context_length``.

        Call this from integration hooks that observe LLM calls
        (e.g. LangGraph's LLM node callback, OpenAI SDK's
        post-completion hook).

        Args:
            prompt: The full prompt text sent to the LLM.
            response: The LLM's completion text.
            input_tokens: Token count for the prompt.
            output_tokens: Token count for the completion.
        """
        total = None
        if input_tokens is not None and output_tokens is not None:
            total = input_tokens + output_tokens

        if prompt:
            self._monitor.check_action(
                agent_id=self.agent_id,
                action="<llm_request>",
                event_type="llm_request",
                metadata={
                    "content": prompt,
                    "args": {
                        "char_count": len(prompt),
                        "system_prompt_present": True,
                    },
                },
            )

        if response:
            self._monitor.check_action(
                agent_id=self.agent_id,
                action="<llm_response>",
                event_type="llm_response",
                metadata={
                    "content": response,
                    "args": {
                        k: v
                        for k, v in {
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "tokens": total,
                        }.items()
                        if v is not None
                    },
                },
            )

    def observe_tool_output(self, tool_name: str, output: str) -> None:
        """Record a tool's output content in the trace.

        Enables atom: ``output_has(tool, regex)``. Call after a tool
        returns its result but before the LLM processes it.

        This is separate from ``guard_after`` which runs the sto
        pipeline. ``observe_tool_output`` only enriches the trace
        with content data — no enforcement, no strategies.

        Args:
            tool_name: Name of the tool that produced the output.
            output: The tool's output text.
        """
        self._monitor.check_action(
            agent_id=self.agent_id,
            action=tool_name,
            event_type="tool_call",
            metadata={"content": output},
        )

    def observe_data_write(self, key: str, fields: list[str] | None = None) -> None:
        """Record a data write event in the trace.

        Enables atoms: ``contains(field)``, ``flow(src, dst)`` (when
        followed by a ``data_read`` from a different agent).

        Args:
            key: Data store key (e.g. ``"customer_db"``, ``"cache"``).
            fields: Field names included in the write payload.
        """
        self._monitor.check_action(
            agent_id=self.agent_id,
            action=f"<data_write:{key}>",
            event_type="data_write",
            metadata={"key": key, "contains": fields},
        )

    def observe_data_read(self, key: str) -> None:
        """Record a data read event in the trace.

        Triggers ``flow(writer_agent, reader_agent)`` if the data was
        written by a different agent.

        Args:
            key: Data store key to read from.
        """
        self._monitor.check_action(
            agent_id=self.agent_id,
            action=f"<data_read:{key}>",
            event_type="data_read",
            metadata={"key": key},
        )

    def observe_delegation(self, to_agent: str) -> None:
        """Record an agent-to-agent delegation (message) event.

        Enables atom: ``delegation_depth``. Call when the current
        agent delegates a task to another agent.

        Args:
            to_agent: The agent receiving the delegated task.
        """
        self._monitor.check_action(
            agent_id=self.agent_id,
            action=f"<delegate:{to_agent}>",
            event_type="message",
            metadata={"to": to_agent},
        )

    # -----------------------------------------------------------------
    # Trace & state management
    # -----------------------------------------------------------------

    @property
    def mode(self) -> str:
        """Enforcement mode: ``"enforce"`` (default) or ``"observe"`` (shadow)."""
        return self._mode

    @property
    def session_log_path(self) -> Path | None:
        """Path to the active JSONL session log, or ``None`` if disabled."""
        if self._session_logger is None:
            return None
        return self._session_logger.path

    @property
    def trace(self) -> Trace:
        """The accumulated runtime trace."""
        return self._monitor.trace

    @property
    def monitor(self) -> RuntimeMonitor:
        """The underlying RuntimeMonitor."""
        return self._monitor

    @property
    def violations(self) -> list[dict]:
        """All recorded violations (det + sto)."""
        return list(self._violations)

    def reset(self) -> None:
        """Clear all state for a fresh session."""
        self._violations.clear()
        self._finish_session_called = False
        self._pending_liveness_violations.clear()
        self._monitor.reset()

    # -----------------------------------------------------------------
    # Ad-hoc verification (non-enforcement query surface)
    # -----------------------------------------------------------------

    def check_nl(self, nl: str, emit_spans: bool = False):
        """Verify an NL rule against the current trace without enforcing.

        Thin wrapper around ``self.monitor.verifier.check_nl(nl)`` that
        handles syncing the verifier to the latest trace state. Returns
        a :class:`~sponsio.runtime.verifier.Verdict` — no strategy is
        applied, no trace mutation, no rollback.

        By default the query is **invisible** to spans / OTEL /
        dashboard — it's an ad-hoc debug query, not part of the enforced
        audit trail. Pass ``emit_spans=True`` to route the query through
        the normal observability pipeline so REPL / notebook debugging
        sessions show up in OTEL backends.

        Args:
            nl: Natural-language rule to check (e.g.
                ``"tool `A` must precede `B`"``). Must parse to a det
                rule; sto rules raise ``ValueError``.
            emit_spans: If ``True``, build a synthetic
                ``AgentTurnSpan(action="<check_nl>")`` containing one
                ``ContractCheckSpan`` + ``GuaranteeSpan`` mirroring the
                verdict, register it in ``monitor.check_spans``, and
                export through OTEL + dashboard. If ``False`` (default),
                produce zero side effects.

        Returns:
            The verifier's :class:`~sponsio.runtime.verifier.Verdict`.

        Raises:
            ValueError: If the NL string cannot be parsed as a det rule.
        """
        # Make sure the verifier sees the current trace state before
        # answering. This is idempotent and cheap thanks to incremental
        # grounding.
        self._monitor.verifier.sync_from_contracts(
            self._monitor.trace, self._system.contracts
        )
        verdict = self._monitor.verifier.check_nl(nl)

        if not emit_spans:
            return verdict

        # --- Debug / visible path: build a span tree and export ---
        from sponsio.models.spans import SpanCollector

        with SpanCollector(agent_id=self.agent_id, action="<check_nl>") as collector:
            collector.start_contract_check(f"adhoc: {verdict.desc}", pipeline="det")
            guar_span = collector.start_guarantee(verdict.desc)
            if verdict.holds:
                collector.finish_span("ok")
                collector.finish_span("ok")  # close contract_check
            else:
                guar_span.result = False
                collector.finish_span("violated")
                collector.add_violation(
                    kind="adhoc",
                    severity="LOW",
                    evidence=f"check_nl returned False for: {nl}",
                )
                collector.finish_span("violated")

            collector.root.total_contracts_checked = 1
            collector.root.det_violations = 0 if verdict.holds else 1
            collector.root.blocked = False  # check_nl never blocks
            collector.root.status = "ok" if verdict.holds else "violated"

        # Surface to consumers of guard.last_check_span / check_spans,
        # then route through OTEL + dashboard pipelines.
        self._monitor._last_turn_span = collector.root
        self._monitor._turn_spans.append(collector.root)

        self._push_to_dashboard("check_nl", content=nl)
        self._otel_export()
        return verdict

    # -----------------------------------------------------------------
    # Session-end checks
    # -----------------------------------------------------------------

    def finish_session(self) -> list:
        """Run end-of-session checks for pending liveness obligations.

        Liveness formulas (e.g. ``always_followed_by(trigger, response)``
        = ``G(called(trigger) -> F(called(response)))``) cannot be
        decided mid-session — at any runtime point, a missing response
        might still arrive later. So :class:`RuntimeMonitor` skips them
        during ``guard_before``.

        Call this method once when the logical agent session is known
        to be complete (after the last user turn, at task exit, in a
        test teardown, etc.). It replays the final trace through
        :class:`~sponsio.runtime.verifier.TraceVerifier` with
        ``include_liveness=True``. The weak finite-trace semantics
        correctly treats any unreached ``F(...)`` as **False** now that
        the trace is finalized — which is exactly when a pending
        obligation becomes a real violation.

        Behavior:

        * **Pure read.** Does not mutate the trace or call any
          strategies. Pending obligations can't be "blocked" after the
          fact — they're reported for audit / metrics / alerting.
        * **Emits a synthetic ``AgentTurnSpan``** (action
          ``"<session_end>"``) containing one ``ContractCheckSpan`` per
          contract with liveness enforcements. Each failing liveness
          enforcement shows up as a ``GuaranteeSpan(result=False)`` with
          a ``ViolationSpan`` + ``EnforcementSpan`` child — exactly the
          same shape as a runtime block, so existing span consumers
          (``guard.last_check_span``, ``guard.check_spans``,
          ``OTelExporter``, dashboard ``/monitor/push``) all pick it up
          with no special-casing.
        * **Emits MonitorEvents** so TerminalReporter and any
          registered callbacks see the violations the same way they see
          runtime ones. The ``EnforcementResult.action`` is
          ``"escalated"`` because a missed liveness obligation needs
          human attention, not an automatic retry.
        * **Routes through OTEL / dashboard pipelines** by calling
          ``_otel_export()`` and ``_push_to_dashboard("session_end")``
          at the end (only if at least one contract was checked) — same
          integration points as ``guard_before`` / ``guard_after``.
        * **Respects assumption gating**: if a contract's assumption
          never held, its liveness enforcement is skipped (the
          obligation was conditional on something that didn't happen).
        * **Idempotent**: calling twice returns the same list and does
          not double-emit spans or events. Call :meth:`reset` if you
          want to re-run after a second session.

        Returns:
            List of :class:`~sponsio.runtime.verifier.Verdict` objects
            for every liveness enforcement that was still unsatisfied
            at session end. Empty if all obligations were discharged,
            or if no liveness constraints exist on this agent.
        """
        from sponsio.models.spans import SpanCollector
        from sponsio.runtime.monitor import MonitorEvent
        from sponsio.runtime.strategies import EnforcementResult

        with self._lock:
            if self._finish_session_called:
                return list(self._pending_liveness_violations)
            self._finish_session_called = True

            failures: list = []

            # Make sure the verifier sees the final trace state.
            agents = {c.agent.id: c.agent for c in self._system.contracts}
            self._monitor.verifier.set_agents(agents)
            self._monitor.verifier.sync_from_contracts(
                self._monitor.trace, self._system.contracts
            )

            # Count liveness-bearing contracts up front so we know
            # whether to emit a session-end span tree at all.
            liveness_contracts = [
                c
                for c in self._system.contracts
                if c.agent.id == self.agent_id
                and any(getattr(e, "liveness", False) for e in c.enforcements)
            ]
            if not liveness_contracts:
                return []

            # Build one synthetic AgentTurnSpan for the whole session-end
            # check. Using SpanCollector keeps the span tree shape
            # identical to runtime turns so every existing consumer
            # (OTEL, dashboard, render_tree, API) works without changes.
            with SpanCollector(
                agent_id=self.agent_id, action="<session_end>"
            ) as collector:
                for contract in liveness_contracts:
                    verdict = self._monitor.verifier.check_contract(
                        contract, include_liveness=True
                    )
                    # Assumption gating — skip liveness if its precondition
                    # never held during the session.
                    if not verdict.assumption_holds:
                        continue

                    a_count = len(contract.assumptions)
                    e_count = len(contract.enforcements)
                    label = (
                        contract.desc
                        or f"{contract.agent.id}: {a_count}A/{e_count}E (liveness)"
                    )
                    collector.start_contract_check(label, pipeline="det")
                    contract_failed = False

                    for e_verdict in verdict.enforcements:
                        formula = e_verdict.formula
                        if not getattr(formula, "liveness", False):
                            # Safety enforcements were already judged at
                            # runtime — skip to avoid double-reporting.
                            continue

                        guar_span = collector.start_guarantee(e_verdict.desc)

                        if e_verdict.holds:
                            collector.finish_span("ok")
                            continue

                        # Failed liveness → build span children + record
                        # violation + emit MonitorEvent.
                        guar_span.result = False
                        collector.finish_span("violated")

                        details = f"Liveness unmet at session end: {e_verdict.desc}"
                        collector.add_violation(
                            kind="liveness",
                            severity="HIGH",
                            evidence=details,
                        )
                        collector.add_enforcement(
                            strategy="LivenessEscalate",
                            result_action="escalated",
                        )

                        failures.append(e_verdict)

                        msg = (
                            f"LIVENESS: {e_verdict.desc} "
                            f"— obligation unmet at session end"
                        )
                        event = MonitorEvent(
                            agent_id=self.agent_id,
                            action="<session_end>",
                            pipeline="det",
                            constraint_name=f"liveness: {e_verdict.desc}",
                            result=EnforcementResult(
                                action="escalated",
                                message=msg,
                            ),
                        )
                        self._monitor._log.append(event)
                        self._monitor._emit(event)
                        self._violations.append(
                            {
                                "tool": "<session_end>",
                                "constraint": f"liveness: {e_verdict.desc}",
                                "action": "ESCALATED",
                            }
                        )
                        contract_failed = True

                    collector.finish_span("violated" if contract_failed else "ok")

                # Populate root-span summary stats just like the monitor
                # does for a normal turn.
                collector.root.total_contracts_checked = sum(
                    1
                    for c in collector.root.children
                    if c.span_type == "sponsio.contract_check"
                )
                collector.root.det_violations = len(failures)
                collector.root.blocked = False  # session-end can't block
                if failures:
                    collector.root.status = "violated"

            # Register the synthetic turn with the monitor so
            # ``guard.last_check_span`` and ``guard.check_spans`` surface it.
            self._monitor._last_turn_span = collector.root
            self._monitor._turn_spans.append(collector.root)

            self._pending_liveness_violations = failures

        # Route through the same OTEL / dashboard paths as runtime checks.
        # Called outside the lock to match ``guard_before`` pattern.
        self._push_to_dashboard("session_end", content=f"{len(failures)} pending")
        self._otel_export()
        return list(failures)

    def summary(self) -> str:
        """Human-readable summary of all violations."""
        if not self._violations:
            return "\u2705 No violations detected."
        lines = [f"\u25e1\u25e0 {len(self._violations)} violation(s) detected:"]
        for v in self._violations:
            lines.append(
                f"  - Tool '{v['tool']}': {v['constraint']} \u2192 {v['action']}"
            )
        return "\n".join(lines)

    def print_summary(self) -> None:
        """Print a session summary to stderr.

        Shows total checks, violations, and overall status.
        Call this at the end of an agent session.
        """
        total = len(self._monitor.turn_spans)
        hard_v = sum(
            1 for v in self._violations if v.get("action") in ("BLOCKED", "ESCALATED")
        )
        soft_v = sum(1 for v in self._violations if v.get("action") == "RETRY")
        colorize = sys.stderr.isatty()

        def _c(code: str, text: str) -> str:
            return f"\033[{code}m{text}\033[0m" if colorize else text

        lines = [
            "",
            _c("1", f"  Sponsio Session Summary ({self.agent_id})"),
            f"  Total checks: {total}  |  Det violations: {hard_v}  |  Sto violations: {soft_v}",
        ]
        if self._violations:
            for v in self._violations:
                tool = v.get("tool", "?")
                constraint = v.get("constraint", "?")
                action = v.get("action", "?")
                icon = _c("31", "\u2717")
                lines.append(f"  {icon} {tool}: {constraint} \u2192 {action}")
            lines.append(
                _c("31;1", f"  \u2717 {len(self._violations)} violation(s) detected")
            )
        else:
            lines.append(_c("32", "  \u2713 All contracts satisfied"))
        lines.append("")
        print("\n".join(lines), file=sys.stderr)

    # -----------------------------------------------------------------
    # Structured observability (span trees)
    # -----------------------------------------------------------------

    @property
    def last_check_span(self) -> AgentTurnSpan | None:
        """Structured span tree from the last ``guard_before()`` or ``guard_after()``."""
        return self._monitor.last_turn_span

    @property
    def check_spans(self) -> list[AgentTurnSpan]:
        """All span trees from this session."""
        return self._monitor.turn_spans

    def render_checks(self, colorize: bool = True) -> str:
        """Pretty-print all check spans from this session.

        Args:
            colorize: Whether to include ANSI color codes.

        Returns:
            Multi-line string with all span trees, separated by blank lines.
        """
        if not self._monitor.turn_spans:
            return ""
        parts = [
            render_tree(span, colorize=colorize) for span in self._monitor.turn_spans
        ]
        return "\n\n".join(parts)
