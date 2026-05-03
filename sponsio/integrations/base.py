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

import atexit
import os
import re
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


# Strip the noisy prefix that runtime/strategies.py prepends to each
# violation message. The ``→ BLOCKED`` suffix we add in print_summary
# already carries the action, so repeating "BLOCKED: agent.tool —" at
# the front just doubles the signal. Observe mode wraps the same
# string in "OBSERVED (would blocked): ..." which made the suffix
# three-deep. One regex handles all three shapes.
_VIOLATION_PREFIX_RE = re.compile(
    r"""^\s*
        (?:OBSERVED\s*\(would\s+\w+\):\s*)?   # optional observe wrapper
        (?:BLOCKED|ESCALATED):\s+             # leading action
        [^\s:]+\.[^\s:]+                      # agent.tool
        \s+[\u2014\-]\s+                      # em-dash or hyphen
        (?:det\s+constraint\s+violated:\s+)?  # boilerplate
    """,
    re.VERBOSE,
)


def _shorten_violation_msg(msg: str) -> str:
    """Drop the prefix that repeats the action / agent in summary rows.

    Leaves already-concise messages (sto, user-typed, external) alone.
    """
    return _VIOLATION_PREFIX_RE.sub("", msg, count=1)


# ---------------------------------------------------------------------------
# PII auto-tagging — lightweight regex detectors used by ``tag_pii=True``.
# These are intentionally conservative; false positives would pollute the
# trace with spurious ``contains(pii)`` predicates.  Each entry maps a
# ``contains`` tag name to the regex that must match anywhere in the
# stringified tool output.  The tag ``pii`` is a generic superset — it
# fires whenever any of the specific classes matches, so users can
# write ``no_data_leak(pii, external)`` without enumerating every class.
# ---------------------------------------------------------------------------

# Cap how much text we scan for PII per call. The credit-card and phone
# regexes contain bounded but nested optional separators (``\d[ -]?``,
# ``[\s-]?``) which on truly pathological input can backtrack badly. A
# fixed cap puts a hard ceiling on scan time regardless of input shape.
# 100 KB is far above any realistic single tool output worth tagging;
# anything bigger is almost certainly a binary/file dump that wasn't
# meant to flow through PII detection.
_MAX_PII_SCAN_CHARS = 100 * 1024

_PII_DETECTORS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # US Social Security Number — strict 3-2-4 digit shape with
    # separators so ordinary 9-digit numbers (order IDs, timestamps)
    # don't false-positive.
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    # Credit card — 13-19 digits with optional spaces/dashes every
    # 4 digits.  No Luhn check (runtime cost), regex is narrow enough.
    (
        "credit_card",
        re.compile(r"\b(?:\d[ -]?){12,18}\d\b"),
    ),
    # Email — the common RFC-lite pattern, case-insensitive.
    (
        "email",
        re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", re.IGNORECASE),
    ),
    # E.164 / North American phone numbers — deliberately strict: must
    # start with ``+`` or ``(`` or a country code.  Avoids matching
    # every 10-digit integer.
    (
        "phone",
        re.compile(
            r"(?:\+\d{1,3}[\s-]?)?(?:\(\d{3}\)\s?|\d{3}[\s-])\d{3}[\s-]?\d{4}\b"
        ),
    ),
    # API keys / secrets — ``sk-``, ``ghp_``, ``AKIA`` prefixes are
    # distinctive enough to be reliable.
    (
        "secret",
        re.compile(
            r"\b(?:sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})\b"
        ),
    ),
)


def _detect_pii_classes(output: Any) -> list[str]:
    """Return the PII classes present in the stringified ``output``.

    Always includes the generic ``pii`` tag when anything matched, so
    users can write ``no_data_leak(pii, external)`` without enumerating
    every specific class.  Returns an empty list on type errors or
    empty input — never raises.
    """
    try:
        text = output if isinstance(output, str) else str(output)
    except Exception:
        return []
    if not text:
        return []
    if len(text) > _MAX_PII_SCAN_CHARS:
        text = text[:_MAX_PII_SCAN_CHARS]
    hits: list[str] = []
    for tag, pattern in _PII_DETECTORS:
        if pattern.search(text):
            hits.append(tag)
    if hits:
        hits.insert(0, "pii")
    return hits


def _resolve_mode(mode: str | None) -> str:
    """Resolve the effective mode from explicit arg + ``SPONSIO_MODE`` env.

    Default is ``observe`` (shadow mode) so that ``pip install sponsio``
    + a guard import is **never** the change that breaks production:
    you opt *in* to enforcement explicitly, not out of it.  Switch
    once your ``sponsio eval`` numbers say the contracts are tight.

    Precedence: env var > explicit argument > default.  The env var
    wins so ops can flip modes per-deploy without a code change
    (``SPONSIO_MODE=enforce`` to roll out, ``=observe`` to revert).
    """
    env = os.environ.get("SPONSIO_MODE")
    resolved = env.strip() if env else (mode or "observe")
    if resolved not in _VALID_MODES:
        raise ValueError(f"mode must be one of {_VALID_MODES}, got {resolved!r}")
    return resolved


# ---------------------------------------------------------------------------
# Unified sto-violation surface
#
# Issue #12: each framework adapter (LangGraph / OpenAI Agents / CrewAI /
# Vercel AI / Claude Agent / Google ADK) wrote its own phrasing for the sto
# retry-feedback message, and LangGraph additionally *raised* while the
# others returned feedback inline. The behavioural split is the serious
# part — raising aborts the agent loop; returning inline lets the model
# self-correct on the next turn, which is the documented sto retry
# strategy. The wording split is just annoying (QA grepping for
# "quality check failed" used to miss half the frameworks).
#
# Both concerns are fixed here, in one place, by:
#
# * ``format_sto_retry_message`` — the canonical message every adapter
#   should hand back to the model when ``needs_retry and feedback``.
# * A documented contract (see ``BaseGuard.guard_after``) that sto
#   violations MUST be surfaced to the model via the adapter's normal
#   result channel (return value / tool-result message / additional
#   context) and MUST NOT raise. Raising is reserved for det blocks.
# ---------------------------------------------------------------------------


def select_agent_message(
    violations: list[EnforcementResult],
    fallback: str = "Contract violation",
) -> str:
    """Pick the best agent-facing string from a violation list.

    Prefers the structured ``EnforcementResult.agent_msg`` (populated
    by :class:`OutcomeBuilder`) over the legacy ``message`` field,
    because ``agent_msg`` is tuned per ``action`` to nudge the LLM
    toward the right reaction (block → abandon, retry → regenerate,
    escalate → wait). Falls back to ``message`` when ``agent_msg`` is
    empty — covers strategies that haven't migrated to the builder
    yet, plus ad-hoc EnforcementResult constructions in test code.

    The legacy ``message`` field intentionally retains the
    ``"BLOCKED: agent.tool — det constraint violated: …"`` prefix
    for log-parsing back-compat. We don't want that prefix injected
    into the LLM's next prompt — that's exactly the reason
    ``agent_msg`` exists. Integrations call this helper to surface
    the agent-facing line, and reach for ``message`` only when
    writing to logs / dashboards.

    Args:
        violations: Det or sto violations from a ``CheckResult``.
        fallback: String returned when the list is empty (no
            violations).

    Returns:
        The first non-empty ``agent_msg``; else the first
        ``message``; else ``fallback``.
    """
    for v in violations:
        if getattr(v, "agent_msg", ""):
            return v.agent_msg
    if violations:
        return violations[0].message
    return fallback


def format_sto_retry_message(feedback: str, original: Any) -> str:
    """Canonical sto-retry feedback string used by every adapter.

    This is the one-liner that surfaces to the LLM when a tool's output
    *succeeded* (no det block) but the sto pipeline flagged it (e.g.
    toxic response, scope leak, injection echo). Keeping it centralised
    means ops can grep a single phrase across LangGraph / OpenAI Agents
    / CrewAI / Claude Agent / Vercel AI / Google ADK logs, and a future
    change to the template fans out to every integration in one commit.

    The format is deliberately plain text — no JSON, no XML tags — so
    that agents in every framework treat it as a regular tool result
    and the self-correct loop triggers without any special schema
    handling on the model side.
    """
    return (
        f"Tool succeeded but output quality check failed. "
        f"Feedback: {feedback}. Original output: {original}"
    )


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
            - **ContractBuilder** — fluent ``contract(...).assume(...).enforce(...)``
              values are normalized through ``to_dict()``.
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
        contracts: list[Any] | None = None,
        config: str | None = None,
        system: System | None = None,
        policy: dict[str, EnforcementStrategy] | None = None,
        sto_evaluator: StoEvaluator | None = None,
        sto_judge: Any | None = None,
        store: Any | None = None,
        dashboard_url: str | None = None,
        otel_exporter: Any | None = None,
        verbose: bool = True,
        verbosity: int = 1,
        auto_summary: bool = True,
        init_banner: bool = True,
        mode: str | None = None,
        session_log_dir: str | Path | None = None,
        tag_outputs: bool = True,
        tag_pii: bool = False,
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
            # Hold onto the ``performance:`` block so we can apply
            # report mode / export path in __init__ below, and size
            # the ring buffer before any checks happen.
            self._perf_config = parsed.performance
        else:
            self._perf_config = None

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
        # Auto-tagging of tool outputs into the trace.  When
        # ``tag_outputs`` is on (default), every ``guard_after`` call
        # emits a ``data_write`` event keyed on the tool name with
        # ``contains=[tool_name]`` so ``no_data_leak`` and other
        # ``contains()``-based contracts bind without manual
        # instrumentation.  When ``tag_pii`` is also on, the tool
        # output is scanned for common PII patterns (SSN, credit card,
        # email, phone) and each detected class is added to the
        # ``contains`` list so users can write contracts against
        # generic tags like ``contains(pii)`` / ``contains(ssn)``.
        self._tag_outputs = tag_outputs
        self._tag_pii = tag_pii

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
                    # Normalise desc to a string so dict-keying works
                    # even when an upstream emitter / parser hands us a
                    # list (observed on starter-pack contracts whose
                    # nl_description was a multi-line list before
                    # serialisation).  Falling through with a list key
                    # raised ``TypeError: unhashable type: 'list'`` at
                    # guard construction.
                    desc_key = e.desc
                    if isinstance(desc_key, list):
                        desc_key = " ".join(str(x) for x in desc_key)
                    elif desc_key is None:
                        desc_key = ""
                    action = self._violation_actions.get(desc_key, "block")
                    if action in ("warn", "log"):
                        self._policy[desc_key] = WarnOnly()
                    else:
                        self._policy[desc_key] = DetBlock()

        # --- Create monitor ---
        self._monitor = RuntimeMonitor(
            system=self._system,
            sto_evaluator=sto_evaluator,
            policy=self._policy,
            mode=self._mode,
            sto_judge=sto_judge,
        )

        # Resize the perf tracker's per-contract ring buffer if the
        # user configured a non-default histogram_size.  Done here
        # (not in RuntimeMonitor.__init__) because the config is
        # guard-level, and we don't want to plumb it through the
        # monitor constructor signature just for one field.
        if self._perf_config is not None:
            from sponsio.runtime.perf import PerformanceTracker

            self._monitor._perf_tracker = PerformanceTracker(
                per_contract_ring_size=self._perf_config.histogram_size,
            )

        # --- Contract banner + terminal reporter ---
        # Print the contract banner at init so users can visually
        # confirm Sponsio is loaded and which rules are active — even
        # with verbose=False, which otherwise looks identical to "no
        # Sponsio at all". Only the per-event reporter is gated by
        # verbose. Callers that render a richer end-of-session view
        # themselves (e.g. ``sponsio demo`` calling ``render_session``
        # directly) can suppress this with ``init_banner=False`` to
        # avoid duplicating the contracts-armed list.
        from sponsio.runtime.terminal import TerminalReporter, print_banner

        contracts_list = list(self._system._contracts)
        if init_banner:
            print_banner(contracts_list)

        if self._verbose:
            reporter = TerminalReporter(
                verbosity=self._verbosity,
                contracts=contracts_list,
            )
            # Banner already printed above; don't let the first event
            # reprint it.
            reporter._header_printed = True
            reporter._build_label_map()
            self._monitor.register_callback(reporter)

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

        # --- Auto-summary on process exit ---
        # Print a one-line "N violations / K checks" summary at process
        # exit so users always get feedback even when they don't call
        # ``guard.print_summary()`` manually. Idempotent via
        # ``_summary_printed`` flag. Disable with ``auto_summary=False``
        # at construction time (or via ``defaults.auto_summary: false``
        # in yaml), or by calling ``guard.disable_auto_summary()`` after
        # the fact.  All three paths land on the same ``_auto_summary``
        # flag.
        self._summary_printed: bool = False
        # Set when print_summary() dispatched to the Rich session-view
        # renderer (which already includes a perf line). Used by
        # _auto_perf_report to skip the legacy perf table — the export
        # + slow-DFA warning still run independently.
        self._rich_view_printed: bool = False
        self._auto_summary: bool = auto_summary
        atexit.register(self._auto_print_summary)

    # -----------------------------------------------------------------
    # Contract construction
    # -----------------------------------------------------------------

    def _build_contracts(
        self,
        agent_model: Agent,
        contracts: list[Any] | None,
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
            to_dict = getattr(entry, "to_dict", None)
            if callable(to_dict):
                entry = to_dict()

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
            # R1 alpha/beta threading — read from dict entry (defaults
            # preserve existing det semantics).
            alpha = float(entry.get("alpha", 1.0))
            beta = float(entry.get("beta", 1.0))
            activate_at = entry.get("activate_at")

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
                    alpha=alpha,
                    beta=beta,
                    activate_at=activate_at,
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
            parsed_items = []
            for item in value:
                parsed = self._parse_constraint(item, user_formulas, soft_constraints)
                if parsed is not None:
                    parsed_items.append(parsed)
            return parsed_items
        return self._parse_constraint(value, user_formulas, soft_constraints)

    def _parse_constraint(
        self,
        value: Any,
        user_formulas: list,
        soft_constraints: list,
    ) -> Any:
        """Parse a single constraint: NL string, DetFormula, or StoFormula.

        Raises:
            ContractSyntaxError: if the string doesn't match the Sponsio
                contract DSL and no LLM extractor is available. Better
                to fail hard at init than to ship a silent no-op.
        """
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
        """Validate dashboard URL to prevent SSRF / metadata exfiltration.

        Hard rejects:

        * non-``http(s)://`` schemes
        * missing or empty hostnames
        * cloud-metadata endpoints (AWS/GCE/Azure IMDS) — these can
          leak short-lived cloud creds and have no legitimate dashboard
          use case.

        Soft warns (does not raise) for loopback / private / link-local
        IP literals. Local addresses are common in dev workflows
        (``http://localhost:9999`` is the default dev dashboard) so we
        don't reject them — but we surface a one-shot ``UserWarning``
        so an operator who mistakenly leaves ``http://10.0.0.5/...`` in
        a prod config gets a visible signal. Set
        ``SPONSIO_STRICT_DASHBOARD_URL=1`` to escalate the warning to
        a hard error in environments where local targets should be
        impossible.
        """
        if url is None:
            return None
        import ipaddress
        import os
        import warnings
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                f"dashboard_url must use http:// or https:// scheme, "
                f"got {parsed.scheme!r}"
            )
        host = parsed.hostname
        if not host:
            raise ValueError("dashboard_url must have a hostname")

        # --- Hard-blocked: cloud metadata endpoints ---
        blocked_hosts = {
            "metadata.google.internal",
            "metadata",
            "169.254.169.254",
            "fd00:ec2::254",
        }
        if host.lower() in blocked_hosts:
            raise ValueError(
                f"dashboard_url hostname {host!r} is blocked (cloud metadata endpoint)"
            )

        # --- Soft warn: loopback / private / link-local IP literals ---
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None

        local_like = False
        reason = ""
        if ip is not None:
            if ip.is_loopback:
                local_like, reason = True, "loopback"
            elif ip.is_private:
                local_like, reason = True, "private (RFC1918)"
            elif ip.is_link_local:
                local_like, reason = True, "link-local"
            elif ip.is_reserved or ip.is_unspecified:
                local_like, reason = True, "reserved/unspecified"
        elif host.lower() in {
            "localhost",
            "ip6-localhost",
            "ip6-loopback",
        }:
            local_like, reason = True, "loopback (by name)"

        if local_like:
            msg = (
                f"dashboard_url {url!r} targets a local-network "
                f"address ({reason}). Trace data, including tool "
                "arguments, will be POSTed there. If this is "
                "intentional (dev dashboard), ignore this warning. "
                "Set SPONSIO_STRICT_DASHBOARD_URL=1 to escalate to "
                "a hard error in production."
            )
            if os.environ.get("SPONSIO_STRICT_DASHBOARD_URL") == "1":
                raise ValueError(msg)
            warnings.warn(msg, UserWarning, stacklevel=3)

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

    def save_trace_for_eval(
        self,
        target_dir: str | Path,
        *,
        label: str = "safe",
        filename: str | None = None,
    ) -> Path:
        """Persist the current runtime trace as a labelled OTLP-JSON
        file that ``sponsio eval`` can replay.

        Closes the loop between observe-mode production runs and the
        eval corpus: call this at session end (or on any
        human-reviewed turn) to capture a real trace into
        ``traces/safe_*.json`` / ``unsafe_*.json`` without leaving
        the running process.  The file follows the eval-corpus shape
        (one labelled trace per file) so mixing synthetic + real
        traces in one corpus Just Works.

        Args:
            target_dir: Directory to write into.  Created if missing.
            label: One of ``"safe"`` / ``"unsafe"`` (mandatory file
                prefix — the eval runner reads labels from filenames,
                not content).  Use ``"safe"`` for nominal runs,
                ``"unsafe"`` when you caught an incident you want
                contracts to block going forward.
            filename: Optional override (without prefix); defaults to
                ``<agent_id>_<monotonic-ns>.json``.  Useful when you
                want a PR or issue ID in the name.

        Returns:
            The written path.
        """
        from sponsio.tracer.otel_writer import trace_to_otlp

        if label not in ("safe", "unsafe"):
            raise ValueError(
                f"label must be 'safe' or 'unsafe' (got {label!r}) — "
                "other values would silently fall out of eval's "
                "confusion-matrix calculation"
            )

        import json as _json
        import time as _time

        out_dir = Path(target_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        if filename is None:
            # Nanosecond-resolution so rapid back-to-back saves don't
            # collide.  Filename format chosen to sort chronologically
            # when users ``ls`` the corpus directory.
            stem = f"{self.agent_id}_{_time.time_ns()}"
        else:
            stem = filename.removesuffix(".json")

        out_path = out_dir / f"{label}_{stem}.json"
        payload = trace_to_otlp(self.trace, agent_id=self.agent_id)
        out_path.write_text(_json.dumps(payload, indent=2))
        return out_path

    # -----------------------------------------------------------------
    # Performance stats
    # -----------------------------------------------------------------

    def performance_stats(self) -> dict:
        """Return the per-check latency + QPS summary for this guard.

        Returns a JSON-serialisable dict with three bucketed views:

        * ``pure_det``   — contracts that provably never touch an LLM
          (the pure-DFA fast path).  This is the bucket that makes
          the "sub-microsecond checks" story quantifiable.
        * ``sto_cached`` — sto contracts whose answer came from the
          per-atom memo this check.  Still no LLM call, just a lookup.
        * ``sto_live``   — sto contracts that actually invoked the
          judge.  One of these per new claim; expect ms-range.

        Plus ``zero_llm_ratio`` = fraction of all checks that were
        either ``pure_det`` or ``sto_cached``.  On a typical session
        where a contract is hit many times after its first sto eval
        caches, this number trends toward 1.0 — which is the single
        most useful number to show alongside "p99 = Xμs".

        Returns:
            Dict shaped like :meth:`PerfSummary.to_dict`. Safe to
            dump straight to JSON; safe to call at any time (no I/O
            side effects, no reset).  Returns a summary with
            ``total_checks=0`` if the guard has never seen an action.
        """
        return self._monitor.performance_tracker.summarize().to_dict()

    def print_performance(self, *, color: bool | None = None) -> None:
        """Pretty-print the performance summary to stdout.

        Convenience over ``print(guard.performance_stats())`` — uses
        the human-readable table renderer from
        :mod:`sponsio.runtime.perf`, auto-detects TTY for colour
        unless ``color`` is explicitly set.
        """
        import sys as _sys

        from sponsio.runtime.perf import format_summary

        effective_color = color if color is not None else _sys.stdout.isatty()
        summary = self._monitor.performance_tracker.summarize()
        print(format_summary(summary, color=effective_color))

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
            if self._mode != "observe" and result.blocked:
                # ``rollback_last_event`` clears the trace event AND every
                # derived cache (verifier valuations, G-cache, DFA progress,
                # per-contract sto atom cache). Doing that as one operation
                # on the monitor avoids the bug where the verifier got
                # reset but the atom cache kept stale per-position scores
                # for the position the next event is about to reuse.
                if self._monitor.rollback_last_event():
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
        # Auto-tag tool output into the trace *before* the sto check
        # so any new ``contains()`` predicates are visible to sto atoms
        # reading them.
        self._autotag_tool_output(tool_name, output)

        with self._lock:
            if self._monitor._sto_evaluator is None:
                return CheckResult(allowed=True)

            # Assumption gating — mirrors ``RuntimeMonitor._check_sto``
            # so that a sto constraint whose owning contract's det
            # *assumption* is currently unmet is skipped here too. Without
            # this, a `contract("on refund").enforce(Atom("tone", ...))`
            # kept flagging retries on turns where no refund was ever
            # mentioned — same behavioural bug `_check_sto` already guards
            # against but that `guard_after` didn't share. See #12
            # follow-up; the two paths must agree on what "active" means.
            self._monitor._verifier.set_agents(
                {c.agent.id: c.agent for c in self._monitor._system.contracts}
            )
            self._monitor._verifier.sync_from_contracts(
                self._monitor.trace, self._monitor._system.contracts
            )
            gated_pass: set[str] = set()
            gated_fail: set[str] = set()
            owned_by_contract: set[str] = set()
            for contract in self._monitor._system.contracts:
                if contract.agent.id != self.agent_id:
                    continue
                if contract.is_unconditional:
                    assumption_holds = True
                else:
                    assumption_holds = self._monitor._verifier.check_assumption(
                        contract
                    ).holds
                for e in contract.enforcements:
                    # ``_is_det`` lives in the verifier module; pre-OSS-cut
                    # ``monitor`` re-exported it via an internal import that
                    # ruff pruned when the sto path was stubbed out. Import
                    # directly from where it actually lives so this guard
                    # stays correct regardless of monitor's internal shape.
                    from sponsio.runtime.verifier import _is_det

                    if _is_det(e):
                        continue
                    prop_name = getattr(e, "desc", str(e))
                    owned_by_contract.add(prop_name)
                    (gated_pass if assumption_holds else gated_fail).add(prop_name)

            checked = self._monitor._sto_evaluator.check(self._monitor.trace)
            sto_violations: list[EnforcementResult] = []
            feedback_parts: list[str] = []

            feedback_gen = FeedbackGenerator()

            for prop_name, (passed, sto_result) in checked.items():
                if passed:
                    continue
                # Unconditional registration (not attached to any contract
                # for this agent) is treated as always-active, matching
                # ``_check_sto``. Conditional props require assumption hold.
                if prop_name in owned_by_contract and prop_name not in gated_pass:
                    continue

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
    ) -> CheckResult:
        """Record an LLM request/response pair in the trace and evaluate
        any contracts that apply to those events.

        Enables atoms: ``prompt_contains``, ``llm_said``,
        ``token_count``, ``system_prompt_present``, ``context_length``,
        and — as of R2 integration — any ``atom_type="sto"`` atom
        whose ``context_scope`` is ``"event"`` or ``"full_trace"``
        (e.g. ``injection_free``, ``scope_respect``, ``no_pii``).

        Call this from integration hooks that observe LLM calls
        (e.g. LangGraph's LLM node callback, OpenAI SDK's
        post-completion hook).

        Args:
            prompt: The full prompt text sent to the LLM.
            response: The LLM's completion text.
            input_tokens: Token count for the prompt.
            output_tokens: Token count for the completion.

        Returns:
            CheckResult aggregating violations from both the request-
            and response-side contracts (det violations first, then sto).
            Callers can inspect ``.all_violations`` for the full list
            including confidence / threshold information.
        """
        total = None
        if input_tokens is not None and output_tokens is not None:
            total = input_tokens + output_tokens

        det_violations: list = []
        sto_violations: list = []

        if prompt:
            results = self._monitor.check_action(
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
            for r in results:
                # Sto pipeline produces "retrying"; det produces
                # blocked / escalated. Split accordingly.
                if r.action == "retrying":
                    sto_violations.append(r)
                elif r.action in ("blocked", "escalated"):
                    det_violations.append(r)

        if response:
            results = self._monitor.check_action(
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
            for r in results:
                if r.action == "retrying":
                    sto_violations.append(r)
                elif r.action in ("blocked", "escalated"):
                    det_violations.append(r)

        allowed = len(det_violations) == 0
        feedback = None
        if sto_violations:
            prompts = [v.retry_prompt for v in sto_violations if v.retry_prompt]
            if prompts:
                feedback = "\n\n".join(prompts)
        return CheckResult(
            allowed=allowed,
            det_violations=det_violations,
            sto_violations=sto_violations,
            feedback=feedback,
        )

    def observe_tool_output(self, tool_name: str, output: str) -> None:
        """Record a tool's output content in the trace.

        Enables atom: ``output_has(tool, regex)``. Call after a tool
        returns its result but before the LLM processes it.

        This is separate from ``guard_after`` which runs the sto
        pipeline. ``observe_tool_output`` only enriches the trace
        with content data — no enforcement, no strategies.

        Implementation note (pre-fix this method called
        ``check_action(event_type="tool_call")`` which (a) ran the full
        det+sto enforcement pipeline, contradicting the docstring, and
        (b) emitted a brand-new ``tool_call`` event that bumped
        ``called(tool)`` / ``call_counts`` again, so any contract like
        ``tool X at most 1 times`` started double-counting the moment
        the operator enriched its output.  The correct shape is to
        attach the content to the *most recent* matching ``tool_call``
        event and re-ground — that way ``output_has`` fires on the next
        check without corrupting call counts.

        Args:
            tool_name: Name of the tool that produced the output.
            output: The tool's output text.
        """
        # Locate the most recent tool_call for this tool on this agent.
        # Walking in reverse so the latest invocation wins when a tool is
        # called multiple times in a turn (operators can only attach
        # output to the *one that just ran*).
        trace = self._monitor.trace
        for ev in reversed(trace.events):
            if (
                ev.event_type == "tool_call"
                and ev.tool == tool_name
                and ev.agent == self.agent_id
            ):
                text = str(output)
                # Concatenate if this tool was already enriched — users
                # streaming chunked output (SSE, partial responses) call
                # us repeatedly for the same tool_call.
                ev.content = text if ev.content is None else (ev.content + text)
                # Force a re-ground on the next check so ``output_has``
                # and ``arg_field_has`` that read ``.content`` see the
                # new text. ``TraceVerifier.sync`` is incremental and
                # won't otherwise re-evaluate a past event; the cheapest
                # way to punch through that cache is ``reset()``. This
                # is not a hot path — enrichment is typically one call
                # per tool per turn, and the next ``check_action`` will
                # re-ground in O(|trace|) which is what we already pay
                # on initial trace build.
                self._monitor._verifier.reset()
                return

        # No prior tool_call — the user called this before the tool
        # actually ran (or for the wrong agent). Warn instead of
        # silently inventing an event with bogus call counts.
        import warnings

        warnings.warn(
            f"observe_tool_output({tool_name!r}): no preceding tool_call "
            f"for this tool on agent {self.agent_id!r}. Output not "
            "attached. Call this *after* the tool actually runs, not "
            "before (and ensure the tool_name matches the original call).",
            stacklevel=2,
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

    def _autotag_tool_output(self, tool_name: str, output: Any) -> None:
        """Emit a ``data_write`` event tagging a tool's output.

        Shared plumbing for every framework adapter: called from
        ``guard_after`` (LangGraph, CrewAI, Claude Agent, Vercel AI,
        OpenAI Agents SDK) and directly by adapters that bypass
        ``guard_after`` (MCP, which only funnels through
        ``check_action``).  When ``tag_outputs`` is disabled, this is a
        no-op.  When ``tag_pii`` is set, the output is regex-scanned
        and every detected class is added to ``contains`` alongside
        the tool name.  Swallows every exception — auto-tagging must
        never break the agent loop.
        """
        if not (self._tag_outputs and tool_name):
            return
        try:
            fields = [tool_name]
            if self._tag_pii:
                fields.extend(
                    cls for cls in _detect_pii_classes(output) if cls not in fields
                )
            self.observe_data_write(key=tool_name, fields=fields)
        except Exception:
            pass

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

    def observe_approval(
        self,
        role: str,
        decision: str = "allow",
        scope: str | None = None,
    ) -> None:
        """Record a HITL approval / denial response.

        Convenience wrapper around :meth:`observe_context` that pushes
        a structured ``approval.*`` block into the current context.
        Subsequent contracts can query it via standard ``ctx_matches``::

            # "refund must be preceded by an active senior_eng approval"
            G(called(refund) → ctx_matches("approval.role", "senior_eng"))
            G(called(refund) → ctx_matches("approval.decision", "allow"))

            # Time-bounded variant — pair with ``time_since`` pattern:
            #   approval valid for 1h after the approver responded
            G(called(refund) →
                Le(Var("time_since", "ctx(approval.role, senior_eng)"), 3600))

        We intentionally don't add a new ``approval_response`` event
        type — every queryable property reduces to ``ctx_matches`` +
        ``time_since`` over the existing ``context_update`` channel.
        Keeping the surface minimal avoids a parallel atom catalogue
        that would have to be mirrored in the TS SDK and the NL parser.

        Args:
            role: The approver's role / identity (e.g. ``"senior_eng"``,
                ``"compliance"``). Pushed as ``ctx(approval.role, role)``.
            decision: ``"allow"`` or ``"deny"`` (free-form — checks use
                regex). Pushed as ``ctx(approval.decision, decision)``.
            scope: Optional action / resource scope this approval covers
                (e.g. ``"refund:>5000"``). Pushed as
                ``ctx(approval.scope, scope)`` when provided.
        """
        facts: dict[str, str] = {
            "approval.role": role,
            "approval.decision": decision,
        }
        if scope:
            facts["approval.scope"] = scope
        self.observe_context(facts)

    def observe_context(self, facts: dict[str, str]) -> None:
        """Push external facts from the host stack into the contract layer.

        Sponsio stays thin on purpose — it doesn't know who the caller
        cryptographically is, where a retrieved RAG chunk came from, or
        whether an inter-agent message was signed. Those are the host
        stack's job (SPIFFE / Okta / C2PA / signed A2A envelopes). This
        hook is the bridge: whatever facts your integration already has,
        push them in and subsequent events will carry them as ``ctx(k, v)``
        atoms. Contracts can then say things like::

            G(called(wire_transfer) → ctx_matches("caller_id", "spiffe://prod/.*"))
            G(called(answer_policy)  → ctx("content_source", "canonical:/v3"))
            G(called(publish)        → ctx("msg_verified", "true"))

        Calls are **merge-on-write** — later ``observe_context`` calls
        override keys seen before but don't clear unrelated ones. To
        clear a key, set it to ``""`` or start a new session. Facts
        persist until overridden, so you typically call this once per
        tool-call boundary (or once per request if the identity is
        stable for the whole session).

        Why this is the hook instead of a dedicated atom per concept:
        every team has their own SOC2 tags / tenant ids / data classes
        / trust tiers. A generic ``ctx(k, v)`` + user-defined keys keeps
        Sponsio's atom surface closed but lets users express arbitrary
        provenance policies.

        Args:
            facts: String-to-string map of external facts to merge into
                the current context. Non-string values are stringified
                by the grounding layer so atom keys stay hashable.
                Keys with ``None`` values are skipped.
        """
        if not facts:
            return
        # Filter None-valued keys at hook time so the trace doesn't
        # store them — keeps the event JSON tight and avoids surprising
        # ``ctx(k, "None")`` strings downstream.
        clean = {k: v for k, v in facts.items() if k is not None and v is not None}
        if not clean:
            return
        self._monitor.check_action(
            agent_id=self.agent_id,
            action="<context_update>",
            event_type="context_update",
            metadata={"args": clean},
        )

    # -----------------------------------------------------------------
    # Trace & state management
    # -----------------------------------------------------------------

    @property
    def mode(self) -> str:
        """Enforcement mode: ``"observe"`` (default, shadow-mode) or ``"enforce"``.

        Precedence when the guard is constructed is ``SPONSIO_MODE`` env
        var > ctor arg > yaml ``runtime.mode`` > ``"observe"``. See
        ``sponsio.core.Sponsio`` for the factory that resolves this.
        """
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

    def rotate_session(
        self,
        *,
        run_finish_session: bool = True,
        require_finish_session: bool = False,
    ) -> dict:
        """Rotate to a fresh session window; return a hand-off summary.

        This is the memory-management primitive for **long-running
        agents** — services that process thousands of tool calls across
        hours or days without an obvious session boundary. Sponsio's
        per-monitor state (``trace.events``, ``_atom_caches``,
        ``_turn_spans``, ``_log``, ``_violations``) grows monotonically
        because that's what whole-trace LTL semantics requires; without
        rotation, a 24-hour customer-service agent eventually sits on
        hundreds of MB of trace data. Rotation caps it without tearing
        up the contract definitions.

        Typical wiring::

            # Every N turns, or every T minutes, or at a business
            # boundary like "end of customer conversation".
            summary = guard.rotate_session()
            audit_logger.info(
                "sponsio.rotate",
                extra={
                    "events": summary["events"],
                    "violations": summary["violations_cleared"],
                    "turns": summary["turns"],
                },
            )

        Liveness handling
        -----------------
        Formulas like ``F(response)`` or
        ``always_followed_by(trigger, response)`` that depend on the
        entire trace **cannot** survive a rotation — the post-rotation
        verifier doesn't see the original ``trigger`` and will never
        report the missed ``response``. To avoid silently swallowing
        these obligations, ``rotate_session`` runs
        :meth:`finish_session` **first by default**, so any pending
        liveness obligation is flushed into violations / spans / the
        audit log *before* the trace is wiped. Set
        ``run_finish_session=False`` if the caller is managing
        finalisation separately (e.g. running ``finish_session`` at a
        different cadence from rotation).

        Parameters
        ----------
        run_finish_session
            When ``True`` (default) run :meth:`finish_session` before
            clearing state, so pending liveness violations are emitted
            normally. Has no effect if ``finish_session`` was already
            called for this window (it's idempotent).
        require_finish_session
            When ``True``, refuse to rotate if ``finish_session``
            hasn't run yet. Useful as a safety net in code paths where
            forgetting to finalise would be a silent bug. Default
            ``False`` — most callers prefer the "just do the right
            thing" behaviour of ``run_finish_session=True``.

        Returns
        -------
        dict
            ``{"events": int, "turns": int, "log_entries": int,
            "violations_cleared": int,
            "pending_liveness_violations": int}`` — the size of the
            window that just closed. ``pending_liveness_violations``
            counts obligations that ``finish_session`` surfaced at
            rotation time (zero if ``run_finish_session=False`` or no
            pending obligations).
        """
        # Preflight: require_finish_session is a read-only check, no
        # lock needed (a concurrent mutation would lose the race
        # anyway, and the caller has asked for strictness).
        if require_finish_session and not self._finish_session_called:
            raise RuntimeError(
                "rotate_session(require_finish_session=True) called "
                "before finish_session. Call finish_session() first so "
                "pending liveness obligations are recorded, or use "
                "rotate_session(run_finish_session=True) to have rotate "
                "call it for you."
            )

        # Step 1 — finalise liveness *outside* ``self._lock`` because
        # ``finish_session`` takes the same (non-reentrant) lock.
        pending_count = 0
        if run_finish_session and not self._finish_session_called:
            try:
                pending = self.finish_session()
                pending_count = len(pending) if pending else 0
            except Exception as exc:  # noqa: BLE001
                # finish_session failures shouldn't stop rotation —
                # the alternative is memory leak. Surface as warning
                # so ops can investigate.
                import warnings

                warnings.warn(
                    f"rotate_session: finish_session raised {exc!r} — "
                    "rotating anyway to bound memory. Pending liveness "
                    "obligations may be lost.",
                    stacklevel=2,
                )
        else:
            pending_count = len(self._pending_liveness_violations)

        # Step 2 — snapshot guard-side counts under the lock, rotate
        # the monitor, clear guard-side state. A concurrent
        # ``guard_before`` either fully observes the old window (if it
        # beat us into ``self._lock``) or fully observes the new
        # window (if it came after) — never a mix.
        with self._lock:
            violation_count = len(self._violations)
            monitor_summary = self._monitor.rotate_session()
            summary = {
                "events": monitor_summary["events"],
                "turns": monitor_summary["turns"],
                "log_entries": monitor_summary["log_entries"],
                "violations_cleared": violation_count,
                "pending_liveness_violations": pending_count,
            }
            # Clear guard-side state. Don't call ``self.reset()`` —
            # that would re-invoke ``monitor.reset()`` on an already
            # cleared monitor and is a waste of a lock acquisition.
            self._violations.clear()
            self._finish_session_called = False
            self._pending_liveness_violations.clear()

        return summary

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
                        # ``_emit`` already appends to ``_log`` under the
                        # monitor lock and fans out to callbacks. The
                        # extra ``_log.append`` that used to precede this
                        # line double-recorded every session-end liveness
                        # event in the audit log and pushed it to OTel /
                        # dashboard exporters twice — caught in the
                        # perf/arch sweep.
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
        lines = [f"\u25d2\u25d3 {len(self._violations)} violation(s) detected:"]
        for v in self._violations:
            lines.append(
                f"  - Tool '{v['tool']}': {v['constraint']} \u2192 {v['action']}"
            )
        return "\n".join(lines)

    def _try_print_rich_session_view(self) -> bool:
        """Render the v1 CLI mockup via :mod:`sponsio.render.session_view`.

        Returns ``True`` if the Rich path ran successfully, ``False``
        if we should fall through to the legacy plain summary
        (non-TTY, NO_COLOR, no spans, or a render-side exception).
        """
        if os.environ.get("NO_COLOR") or not sys.stderr.isatty():
            return False
        turn_spans = list(self._monitor.turn_spans)
        if not turn_spans:
            # An empty session has nothing tree-like to show; legacy
            # one-liner ("✓ All contracts satisfied") fits better.
            return False
        try:
            from rich.console import Console

            from sponsio.render.session_view import render_session

            console = Console(file=sys.stderr, soft_wrap=True, highlight=False)
            render_session(
                console=console,
                agent_id=self.agent_id,
                mode=self._mode,
                contracts=list(self._system._contracts),
                turn_spans=turn_spans,
            )
            self._rich_view_printed = True
            return True
        except Exception:
            # Never let a render bug swallow the summary.
            return False

    def disable_auto_summary(self) -> None:
        """Suppress the atexit auto-summary for this guard.

        Useful in server / test contexts where stderr noise at shutdown
        isn't wanted.
        """
        self._auto_summary = False

    def _auto_print_summary(self) -> None:
        """atexit hook — print the session summary exactly once if enabled."""
        if not self._auto_summary or self._summary_printed:
            return
        # ``verbose=False`` means "stay silent" — the contract banner
        # at init is the one exception (a deliberate "Sponsio is loaded"
        # signal), but any later auto-emit including the session summary
        # MUST honour it.  Without this, doctor's internal smoke-test
        # guard (verbose=False) leaks a "Sponsio Session Summary
        # (doctor)" line at process exit even though stderr was
        # redirected during the smoke cycle itself.
        if not self._verbose:
            return
        # Only auto-print to an interactive terminal. Under pytest / CI /
        # redirected stderr we stay silent so we don't clutter test output
        # or production server logs; users can still call print_summary()
        # explicitly.
        if not sys.stderr.isatty():
            return
        # Be defensive: never let shutdown logging raise.
        try:
            self.print_summary()
        except Exception:
            pass
        # Separately honour ``performance.report`` / ``performance.export_path``.
        # Done in its own try/except so a perf-report failure can't
        # mask the main session summary.
        try:
            self._auto_perf_report()
        except Exception:
            pass

    def _auto_perf_report(self) -> None:
        """Apply the YAML ``performance:`` section at process exit.

        Three independent side effects, each gated by its own config:

        1. ``report=always|auto|never`` → pretty-print the table.
           ``auto`` follows the same "verbose + TTY" convention as
           the session summary so CI logs stay clean by default.
        2. ``export_path`` → dump the JSON summary.  Writes are
           idempotent and atomic (via ``PerformanceTracker.export_json``
           which creates parent dirs); a re-run overwrites cleanly.
        3. ``warn_slow_dfa_us`` → one-line stderr warning if the
           pure-DFA p99 exceeded the budget (disabled when the value
           is ≤0).  Catches the common "this contract accidentally
           went through the sto pipeline" footgun — DFA checks should
           stay far below sto/LLM latency.
        """
        cfg = self._perf_config
        report_mode = "auto" if cfg is None else cfg.report

        summary = self._monitor.performance_tracker.summarize()
        if summary.total_checks == 0:
            # Don't spam "0 checks recorded" in every hello-world
            # script; only meaningful when the guard actually saw
            # traffic.  Also short-circuits export/warn when there's
            # literally nothing to report on — same reason.
            return

        # Print is gated by ``report=``, export and warn are
        # independent side-effects so ``report: never +
        # export_path: foo.json`` still writes the JSON (common
        # pattern: dashboards consume perf.json, humans don't need
        # the stderr table).
        should_print = report_mode == "always" or (
            report_mode == "auto"
            and self._verbose
            and sys.stderr.isatty()
            # The Rich session view already includes its own perf line
            # (35 checks / det% / p50 / p99 / max). Re-printing the
            # legacy table right after would duplicate the same numbers
            # in two visually-incompatible formats. Skip when the new
            # view ran; export + warn below still fire.
            and not self._rich_view_printed
        )
        if should_print:
            from sponsio.runtime.perf import format_summary

            print(format_summary(summary, color=sys.stderr.isatty()), file=sys.stderr)

        if cfg is not None and cfg.export_path:
            try:
                self._monitor.performance_tracker.export_json(cfg.export_path)
            except OSError as exc:
                print(
                    f"[sponsio] perf export to {cfg.export_path!r} failed: {exc}",
                    file=sys.stderr,
                )

        if cfg is not None and cfg.warn_slow_dfa_us > 0 and summary.pure_det.n > 0:
            p99_us = summary.pure_det.p99_ns / 1_000.0
            if p99_us > cfg.warn_slow_dfa_us:
                print(
                    f"[sponsio] warning: pure-DFA p99 = {p99_us:.1f}μs "
                    f"exceeds configured threshold of {cfg.warn_slow_dfa_us:.1f}μs. "
                    f"Something in your contract set may be accidentally "
                    f"hitting the sto pipeline — inspect guard.performance_stats() "
                    f"by contract.",
                    file=sys.stderr,
                )

    def print_summary(self) -> None:
        """Print a session summary to stderr.

        Shows total checks, violations, and overall status. Auto-called
        at process exit (see ``__init__``); can also be invoked manually.
        Idempotent — subsequent calls are no-ops.

        On a TTY this dispatches to the Rich session-view renderer
        (the v1 CLI mockup form: header banner + contracts armed +
        trace tree + verdict banner + perf summary + CTA). When stderr
        is piped or NO_COLOR is set, falls through to the legacy
        plain-ANSI summary so logs / CI captures stay readable.
        """
        if self._summary_printed:
            return
        if self._try_print_rich_session_view():
            self._summary_printed = True
            return
        self._summary_printed = True
        total = len(self._monitor.turn_spans)
        # BLOCKED (enforce mode) and OBSERVED (observe / shadow mode)
        # are both user-facing signals — the latter is the whole point
        # of observe mode, so it must not be filtered out. RETRY covers
        # sto violations. ``ESCALATED`` is almost always the
        # "assumption not yet fired" monitor event — framework noise,
        # not a contract breach — and stays hidden here.
        shown = [
            v
            for v in self._violations
            if v.get("action") in ("BLOCKED", "OBSERVED", "RETRY")
        ]
        hard_v = sum(1 for v in shown if v.get("action") in ("BLOCKED", "OBSERVED"))
        observed_v = sum(1 for v in shown if v.get("action") == "OBSERVED")
        soft_v = sum(1 for v in shown if v.get("action") == "RETRY")
        colorize = sys.stderr.isatty()

        def _c(code: str, text: str) -> str:
            return f"\033[{code}m{text}\033[0m" if colorize else text

        # Include a dedicated "Would-block" counter in observe mode so
        # shadow rollouts can see the would-have-blocked count at a
        # glance — previously this was silently rolled into 0.
        det_label = "Would-block" if self._mode == "observe" else "Det violations"
        header = (
            f"  Total checks: {total}  |  "
            f"{det_label}: {hard_v}  |  Sto violations: {soft_v}"
        )

        lines = [
            "",
            _c("1", f"  Sponsio Session Summary ({self.agent_id})"),
            header,
        ]
        if shown:
            for v in shown:
                tool = v.get("tool", "?")
                constraint = _shorten_violation_msg(str(v.get("constraint", "?")))
                action = v.get("action", "?")
                icon = (
                    _c("33", "\u26a0")  # yellow warning for OBSERVED
                    if action == "OBSERVED"
                    else _c("31", "\u2717")  # red ✗ for BLOCKED / RETRY
                )
                lines.append(f"  {icon} {tool}: {constraint} \u2192 {action}")
            summary_icon = _c("31;1", "\u2717")
            if observed_v and observed_v == hard_v:
                # Pure-observe session: distinguish messaging so users
                # understand nothing was actually enforced.
                lines.append(
                    _c(
                        "33;1",
                        f"  \u26a0 {observed_v} would-have-blocked event(s) detected (observe mode)",
                    )
                )
            else:
                lines.append(f"  {summary_icon} {len(shown)} violation(s) detected")
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
