"""Grounding: convert a raw Trace into per-timestep predicate truth values.

This is the bridge between the runtime world (Events) and the formal world
(formula evaluation).  The evaluator operates on ``list[dict[str, bool]]``
-- it has no idea what a "tool call" is.  Grounding translates:

    Event(tool_call, tool="fraud_check")  -->  {"called(fraud_check)": True}

Both this module and ``formulas/formula.py`` generate predicate key strings
via the shared ``_pred_key.pred_key()`` function, ensuring the keys always
match.  See ``_pred_key.py`` for details.

Predicate catalogue (what this module can currently extract):

    Tool call layer:
        called(X)                 -- tool X was called at this timestep
        count(X)                  -- cumulative invocation count (int, LTL can't count)
        perm(P)                   -- the acting agent holds permission P (static)
        arg_has(tool, pattern)    -- tool args (serialized) match regex ``pattern``
        arg_field_has(tool, field, pattern) -- specific arg field matches regex ``pattern``
        arg_length_exceeds(tool, field, N) -- arg field length > N chars (injection detection)
        arg_paths_within(tool, *prefixes) -- all paths in tool args within allowed prefixes
        contains(field)           -- a data_write event included ``field``
        flow(src, dest)           -- data flowed from agent ``src`` to ``dest``

    P0 — Tool output (requires content in guard_after):
        output_has(tool, pattern) -- tool output matches regex ``pattern``

    P1 — LLM response (requires llm_response event from integration hook):
        llm_said(pattern)         -- LLM output matches regex ``pattern``

    P2 — LLM request (requires llm_request event from integration hook):
        prompt_contains(pattern)  -- LLM input matches regex ``pattern``
        system_prompt_present()   -- LLM request has a system message (structural)
        context_length()          -- total char count of LLM input (int)

    Time layer (event clock — replay-deterministic, not wall-clock):
        now                       -- ts of the current event (float)
        time_since(predicate_key) -- seconds since ``predicate_key`` last
                                     emitted true; sentinel ``1e18`` if
                                     never. Sentinel chosen so any
                                     ``Le(time_since(P), N)`` fails
                                     correctly when P never happened —
                                     "never" is semantically "very long
                                     ago", not "just now". Requested via
                                     ``content_atoms["time_since"]``.

    Args conventions (no event-type schema change):
        data_write.args["scope"]    "internal" | "external" (default
                                     "external"). Internal writes don't
                                     register in ``data_stores`` so a
                                     later cross-agent read won't fire
                                     ``flow()``. ``contains()`` still
                                     emits regardless.
        llm_response.args["segment"] "thinking" | "answer" | other label.
                                     Emits ``segment(value)`` atom at
                                     the timestep so contracts can scope
                                     ``llm_said``-style checks (e.g.
                                     ``Implies(segment("answer"),
                                     ~llm_said(secret))``).

    Parameterized atoms (arg_has, arg_paths_within, output_has, llm_said,
    prompt_contains, time_since) require the caller to pass
    ``content_atoms`` from ``collect_content_atoms()`` so grounding knows
    which patterns to check.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field

from sponsio.models.trace import Event, Trace
from sponsio.formulas._pred_key import pred_key


@dataclass
class GroundingState:
    """Persistent accumulators used by incremental grounding.

    Grounding is fundamentally a streaming operation — several predicates
    (``count``, ``flow``, ``count_with``, ``data_stores``) depend on the
    history of events seen so far, not just the current one. This
    dataclass captures all of that state so callers (notably
    :class:`sponsio.runtime.verifier.Verifier`) can hold one instance
    across many ``ground_event`` calls and avoid re-scanning the whole
    trace every time.

    The batch :func:`ground` function creates a fresh ``GroundingState``
    internally; incremental callers pass their own long-lived instance.

    Attributes:
        call_counts: Cumulative ``count(tool)`` across the trace seen so far.
        call_with_counts: Cumulative ``count_with(tool, pattern)`` for
            logical-operation matching.
        data_stores: ``key -> {"agent", "contains", "ts"}`` for every
            ``data_write`` observed. Used to derive ``flow()`` on later reads.
        flow_pairs: Set of ``(src, dst)`` agent pairs that ever flowed.
        active_flows: Pre-encoded ``"flow(src, dst)"`` keys that should
            be forward-propagated to every future timestep. Once a flow
            becomes active it stays active for the rest of the session.
    """

    call_counts: dict[str, int] = field(default_factory=dict)
    call_with_counts: dict[tuple[str, str], int] = field(default_factory=dict)
    data_stores: dict[str, dict] = field(default_factory=dict)
    flow_pairs: set[tuple[str, str]] = field(default_factory=set)
    active_flows: set[str] = field(default_factory=set)
    # External facts pushed by the integration via
    # ``guard.observe_context({...})`` — caller identity, retrieved-
    # content source, signed-message metadata, SOC2 scope, anything
    # the host stack wants to expose to contracts. Persists across
    # events until a later ``context_update`` event merges in new
    # keys (or overwrites existing ones). Re-emitted as ``ctx(k, v)``
    # atoms at every subsequent event so ``G(called(x) → ctx(k, v))``
    # fires as expected.
    current_ctx: dict[str, str] = field(default_factory=dict)
    # Pre-encoded ``"contains(field)"`` keys that should be
    # forward-propagated to every future timestep.  ``contains`` is
    # set on a ``data_write`` event; without forward propagation the
    # predicate would be true for exactly that one timestep, which
    # makes formulas like ``no_data_leak`` —
    # ``G(contains(x) -> !flow(x, ext))`` — practically unfireable
    # because the read/send happens on a *later* timestep.
    active_contains: set[str] = field(default_factory=set)
    # Layer 2 accumulators
    token_count: dict[str, int] = field(default_factory=dict)  # L2.1
    delegation_depth: int = 0  # L2.4
    consecutive_counts: dict[str, int] = field(default_factory=dict)  # L1.4
    last_tool: str = ""  # previous tool name for consecutive detection
    # Event-clock primitives. ``now`` is the ts of the most recently
    # grounded event; ``last_ts[predicate_key]`` is the ts of the last
    # event where ``predicate_key`` *transitioned* False→True. We
    # track ``true_at_prev`` (the predicate keys true at the previous
    # event, after forward-propagation) so sustained predicates —
    # propagated ``flow``/``contains`` and re-emitted ``ctx(k, v)``
    # facts — do NOT refresh ``last_ts`` while they remain held. This
    # is the difference that makes ``time_since(ctx(approval.role,
    # alice))`` measure "time since the approval was granted" rather
    # than the trivial 0 it would be if every re-emission counted.
    # Feeds the ``time_since(key)`` derived atom; ``last_ts`` is
    # internal-only (not exposed as Var) so callers don't accidentally
    # rely on its missing-key default.
    now: float = 0.0
    last_ts: dict[str, float] = field(default_factory=dict)
    true_at_prev: set[str] = field(default_factory=set)

    def reset(self) -> None:
        """Clear all accumulators. Called on rollback / new session."""
        self.call_counts.clear()
        self.call_with_counts.clear()
        self.data_stores.clear()
        self.flow_pairs.clear()
        self.active_flows.clear()
        self.active_contains.clear()
        self.token_count.clear()
        self.delegation_depth = 0
        self.consecutive_counts.clear()
        self.last_tool = ""
        self.current_ctx.clear()
        self.now = 0.0
        self.last_ts.clear()
        self.true_at_prev.clear()


_NAMESPACED_TOOL_RE = re.compile(r"^[A-Za-z_][\w-]*:[A-Za-z_][\w-]*$")


def _tool_matches(target_tool: str, event_tool: str, args_str: str) -> bool:
    """Check if a target tool spec matches the current event.

    Supports two ``tool:`` forms (kept in sync with the heuristic in
    ``sponsio.patterns.library._is_namespaced_tool_name``):

    * ``tool:argpattern`` (legacy) — physical tool name must match AND
      args must regex-contain the pattern. Detected by the RHS having
      whitespace / regex metacharacters.
    * ``plugin:skill`` (Claude Code namespaced skill / MCP-style) —
      the whole string is a literal tool name. Detected by both sides
      being bare identifiers (``[A-Za-z_][\\w-]*``).

    Plain tool names with no ``:`` match directly.
    """
    if ":" in target_tool and not _NAMESPACED_TOOL_RE.match(target_tool):
        physical, pattern = target_tool.split(":", 1)
        return physical == event_tool and bool(re.search(pattern, args_str))
    return target_tool == event_tool


# Atom predicates that require regex matching against event content.
# Grounding must know which patterns to check; callers extract these
# from the formula tree via ``collect_content_atoms()``.
_CONTENT_PREDICATES = frozenset(
    {
        "llm_said",  # P1: match against llm_response content
        "prompt_contains",  # P2: match against llm_request content
        "output_has",  # P0: match against tool output content
        "arg_has",  # tool call: match against serialized args
        "arg_field_has",  # tool call: match against a specific arg field
        "arg_paths_within",  # tool call: all paths in args within allowed prefixes
        "arg_numeric",  # tool call: extract numeric value from arg field → int
        "called_with",  # tool call: tool + args pattern = logical operation
        "count_with",  # tool call: cumulative count of tool + args pattern
        "arg_length_exceeds",  # tool call: arg field length > threshold
        # ``ctx_matches(key, pattern)`` checks the **current** external-
        # fact dict (populated by ``observe_context``) for a given key
        # against a regex. Listed here so ``collect_content_atoms``
        # extracts the ``(key, pattern)`` tuples that ``ground_event``
        # needs at runtime.
        "ctx_matches",
        # ``time_since(predicate_key)`` is a derived numeric Var that
        # ``ground_event`` computes from ``state.now`` and
        # ``state.last_ts[predicate_key]``. We pre-collect the requested
        # keys via ``content_atoms`` so grounding only emits the keys
        # that contracts actually reference (rather than every tracked
        # ``last_ts`` entry, which would bloat valuations).
        "time_since",
    }
)


def collect_content_atoms(formulas) -> dict[str, set[tuple[str, ...]]]:
    """Extract content-matching atoms from a collection of formulas.

    Walks the formula AST and collects every ``Atom`` whose predicate
    is in ``_CONTENT_PREDICATES``.  Returns a dict mapping predicate
    name to the set of arg tuples found.

    Example::

        >>> from sponsio.formulas.formula import Atom, G, Not
        >>> f = G(Not(Atom("llm_said", "ignore previous")))
        >>> collect_content_atoms([f])
        {"llm_said": {("ignore previous",)}}

    Args:
        formulas: Iterable of Formula or DetFormula objects.

    Returns:
        Dict mapping predicate name to set of arg tuples.
    """
    from sponsio.formulas.formula import Atom as AtomType, Var as VarType

    result: dict[str, set[tuple[str, ...]]] = {}

    def _walk(node):
        if node is None:
            return
        # Unwrap DetFormula
        if hasattr(node, "formula"):
            _walk(node.formula)
            return
        if isinstance(node, AtomType):
            if node.predicate in _CONTENT_PREDICATES:
                result.setdefault(node.predicate, set()).add(node.args)
            return
        # Var nodes: count_with / count are used inside Le/Ge etc.
        # count_with needs content-atom extraction just like called_with.
        if isinstance(node, VarType):
            if node.name in _CONTENT_PREDICATES:
                result.setdefault(node.name, set()).add(node.args)
            return
        # Recurse into children of composite formula nodes
        for attr in ("child", "left", "right"):
            child = getattr(node, attr, None)
            if child is not None:
                _walk(child)

    for f in formulas:
        _walk(f)
    return result


def ground_event(
    event: Event,
    idx: int,
    state: GroundingState,
    content_atoms: dict[str, set[tuple[str, ...]]] | None = None,
    agents: dict[str, object] | None = None,
) -> dict[str, bool | int]:
    """Ground a single event, mutating ``state`` and returning its valuation.

    This is the per-event kernel of grounding. Both the batch
    :func:`ground` and the incremental
    :class:`sponsio.runtime.verifier.Verifier` call this — the batch
    version builds a fresh :class:`GroundingState`, the verifier keeps
    a long-lived one across events.

    The returned dict contains every predicate that is true (or has a
    numeric value) at this timestep, including:

    * Per-event atoms from this event (``called``, ``contains``, etc.)
    * Cumulative counters (``count``, ``count_with``) as-of this event
    * All active ``flow()`` predicates forward-propagated up to now
    * Static ``perm()`` predicates derived from ``agents``

    Args:
        event: The event to ground.
        idx: Its index in the trace (used for warnings).
        state: Mutable grounding accumulators. Updated in place.
        content_atoms: Which parameterized atoms to evaluate against
            event content. See :func:`collect_content_atoms`.
        agents: Optional agent map for permission lookups.

    Returns:
        The valuation dict for this timestep.
    """
    v: dict[str, bool | int] = {}

    # Advance the event clock. Used by the ``now`` Var and as the
    # numerator for ``time_since(key)``. Stored on state so that an
    # incremental verifier holding a long-lived GroundingState sees
    # the same view between events.
    state.now = float(event.ts)

    # ── tool_call ──────────────────────────────────────────────
    if event.event_type == "tool_call" and not event.tool:
        warnings.warn(
            f"Event {idx}: tool_call with missing tool name "
            f"(agent={event.agent!r}). Predicates will not fire for this event.",
            stacklevel=2,
        )
    if event.event_type == "tool_call" and event.tool:
        v[pred_key("called", event.tool)] = True
        # ``called_any`` — true at any timestep where SOME tool fires,
        # regardless of which.  Used by ``tool_allowlist`` to gate
        # ``G(called_any -> Or(called(t₁)..called(tₙ)))`` so the rule
        # is vacuously satisfied at empty / non-tool-call timesteps.
        # (Matches ``Atom("called_any")`` in TS grounding.)
        v[pred_key("called_any")] = True

        # L1.4: consecutive_count — track how many times the same tool
        # has been called in an unbroken run. Resets when a different
        # tool is called. Used by loop_detection pattern.
        if event.tool == state.last_tool:
            state.consecutive_counts[event.tool] = (
                state.consecutive_counts.get(event.tool, 1) + 1
            )
        else:
            # Different tool → reset the previous tool's consecutive count
            if state.last_tool:
                state.consecutive_counts[state.last_tool] = 0
            state.consecutive_counts[event.tool] = 1
        state.last_tool = event.tool

        state.call_counts[event.tool] = state.call_counts.get(event.tool, 0) + 1

        args_str = str(event.args) if event.args else ""

        # ── called_with / count_with — logical operation matching ──
        cw_patterns: set[tuple[str, ...]] = set()
        if content_atoms:
            cw_patterns |= content_atoms.get("called_with", set())
            cw_patterns |= content_atoms.get("count_with", set())
        if cw_patterns:
            for args_tuple in cw_patterns:
                if len(args_tuple) >= 2:
                    target_tool, pattern = args_tuple[0], args_tuple[1]
                    if target_tool == event.tool:
                        matched = bool(re.search(pattern, args_str))
                        v[pred_key("called_with", *args_tuple)] = matched
                        if matched:
                            cw_key = (target_tool, pattern)
                            state.call_with_counts[cw_key] = (
                                state.call_with_counts.get(cw_key, 0) + 1
                            )

        # ── arg_has(tool, pattern) — regex on serialized args ───
        if args_str and content_atoms and "arg_has" in content_atoms:
            for args_tuple in content_atoms["arg_has"]:
                if len(args_tuple) >= 2:
                    target_tool = args_tuple[0]
                    if _tool_matches(target_tool, event.tool, args_str):
                        matched = bool(re.search(args_tuple[1], args_str))
                        v[pred_key("arg_has", *args_tuple)] = matched

        # ── arg_field_has(tool, field, pattern) — regex on specific arg field ─
        if event.args and content_atoms and "arg_field_has" in content_atoms:
            for args_tuple in content_atoms["arg_field_has"]:
                if len(args_tuple) >= 3:
                    target_tool = args_tuple[0]
                    if _tool_matches(target_tool, event.tool, args_str):
                        field, pattern = args_tuple[1], args_tuple[2]
                        field_val = event.args.get(field)
                        if field_val is not None:
                            matched = bool(re.search(pattern, str(field_val)))
                        else:
                            matched = False
                        v[pred_key("arg_field_has", *args_tuple)] = matched

        # ── arg_length_exceeds(tool, field, max_chars) — field too long ──
        if event.args and content_atoms and "arg_length_exceeds" in content_atoms:
            for args_tuple in content_atoms["arg_length_exceeds"]:
                if len(args_tuple) >= 3:
                    target_tool, field = args_tuple[0], args_tuple[1]
                    if _tool_matches(target_tool, event.tool, args_str):
                        try:
                            max_chars = int(args_tuple[2])
                        except (ValueError, TypeError):
                            max_chars = 500
                        field_val = event.args.get(field, "")
                        exceeded = len(str(field_val)) > max_chars
                        v[pred_key("arg_length_exceeds", *args_tuple)] = exceeded

        # ── arg_numeric(tool, field) — extract numeric value from args ──
        # Enables G(Le(Var(arg_numeric, tool, field), Const(N))) style
        # range constraints. Tries three extraction strategies:
        #   1. Direct dict lookup: event.args[field]
        #   2. CLI flag: --field VALUE in serialized command string
        #   3. Positional: field="N" → Nth whitespace-separated number
        if content_atoms and "arg_numeric" in content_atoms:
            for args_tuple in content_atoms["arg_numeric"]:
                if len(args_tuple) >= 2:
                    target_tool, field = args_tuple[0], args_tuple[1]
                    if _tool_matches(target_tool, event.tool, args_str):
                        numeric_val = None
                        # Strategy 1: direct dict key
                        if event.args and field in event.args:
                            try:
                                numeric_val = int(event.args[field])
                            except (ValueError, TypeError):
                                try:
                                    numeric_val = float(event.args[field])
                                except (ValueError, TypeError):
                                    pass
                        # Strategy 2: CLI --field VALUE
                        if numeric_val is None and args_str:
                            m = re.search(
                                rf"--{re.escape(field)}\s+([+-]?\d+(?:\.\d+)?)",
                                args_str,
                            )
                            if m:
                                try:
                                    numeric_val = int(m.group(1))
                                except ValueError:
                                    numeric_val = float(m.group(1))
                        # Strategy 3: positional (field = digit → Nth whitespace token from command)
                        if numeric_val is None and event.args and field.isdigit():
                            cmd_str = event.args.get("command", "")
                            if cmd_str:
                                tokens = cmd_str.split()
                                pos = int(field)
                                # Count from end if negative-looking, otherwise from start
                                if pos < len(tokens):
                                    try:
                                        numeric_val = int(tokens[pos])
                                    except ValueError:
                                        try:
                                            numeric_val = float(tokens[pos])
                                        except ValueError:
                                            pass
                                # Also try from the end (field="-1" → last token)
                                if numeric_val is None and tokens:
                                    try:
                                        numeric_val = int(tokens[-1])
                                    except ValueError:
                                        pass
                        if numeric_val is not None:
                            v[pred_key("arg_numeric", *args_tuple)] = numeric_val

        # ── arg_paths_within(tool, *prefixes) — all paths in allowed set ─
        if args_str and content_atoms and "arg_paths_within" in content_atoms:
            for args_tuple in content_atoms["arg_paths_within"]:
                if len(args_tuple) >= 2:
                    target_tool = args_tuple[0]
                    if _tool_matches(target_tool, event.tool, args_str):
                        prefixes = args_tuple[1:]
                        paths = re.findall(r'(/[^\s;|&>"\']+)', args_str)
                        if not paths:
                            v[pred_key("arg_paths_within", *args_tuple)] = True
                        else:
                            all_within = all(
                                any(p.startswith(pfx) for pfx in prefixes)
                                for p in paths
                            )
                            v[pred_key("arg_paths_within", *args_tuple)] = all_within

        # ── P0: output_has(tool, pattern) — regex on tool output ─
        if (
            event.content is not None
            and content_atoms
            and "output_has" in content_atoms
        ):
            content_str = str(event.content)
            for args_tuple in content_atoms["output_has"]:
                if len(args_tuple) >= 2:
                    target_tool = args_tuple[0]
                    if _tool_matches(target_tool, event.tool, args_str):
                        matched = bool(re.search(args_tuple[1], content_str))
                        v[pred_key("output_has", *args_tuple)] = matched

    # ── P1: LLM response — llm_said(pattern) ──────────────────
    elif event.event_type == "llm_response":
        if event.content and content_atoms and "llm_said" in content_atoms:
            content_str = str(event.content)
            for args_tuple in content_atoms["llm_said"]:
                if args_tuple:
                    pattern = args_tuple[0]
                    matched = bool(re.search(pattern, content_str))
                    v[pred_key("llm_said", *args_tuple)] = matched
        # P2: Response length — always populated on llm_response events.
        # These are unparameterized Var keys consumed by max_length() pattern.
        if event.content:
            content_str = str(event.content)
            v["response_words"] = len(content_str.split())
            v["response_chars"] = len(content_str)
        # ``args["segment"]`` convention: when an integration can
        # distinguish CoT thinking from the final answer (Claude
        # extended thinking, OpenAI o1 reasoning summaries) it tags
        # the llm_response with ``segment="thinking"`` or
        # ``segment="answer"``. We emit ``segment(value)`` so contracts
        # can scope checks to one segment, e.g.
        # ``G(segment("answer") → ~llm_said(<internal-token>))``.
        if event.args:
            seg = event.args.get("segment")
            if isinstance(seg, str) and seg:
                v[pred_key("segment", seg)] = True

    # ── P2: LLM request — prompt_contains(pattern) + structural ─
    elif event.event_type == "llm_request":
        if event.args and event.args.get("system_prompt_present"):
            v[pred_key("system_prompt_present")] = True
        if event.args and event.args.get("char_count"):
            v[pred_key("context_length")] = event.args["char_count"]

        if event.content and content_atoms and "prompt_contains" in content_atoms:
            content_str = str(event.content)
            for args_tuple in content_atoms["prompt_contains"]:
                if args_tuple:
                    pattern = args_tuple[0]
                    matched = bool(re.search(pattern, content_str))
                    v[pred_key("prompt_contains", *args_tuple)] = matched

    # ── data_write ─────────────────────────────────────────────
    elif event.event_type == "data_write" and not event.key:
        warnings.warn(
            f"Event {idx}: data_write with missing key (agent={event.agent!r}). "
            "contains() predicates will not fire.",
            stacklevel=2,
        )
    elif event.event_type == "data_write" and event.key:
        # ``args["scope"]`` convention: ``"internal"`` flags writes to
        # an agent's own scratchpad / framework state — they should
        # NOT register in ``data_stores``, because a later cross-agent
        # ``data_read`` against an internal write doesn't model a real
        # data exfiltration boundary. ``contains()`` still emits so
        # PII / sensitive-field detection works on internal payloads
        # (you may still want to forbid PII showing up in scratchpad).
        scope = "external"
        if event.args:
            raw_scope = event.args.get("scope")
            if isinstance(raw_scope, str) and raw_scope:
                scope = raw_scope
        if scope != "internal":
            state.data_stores[event.key] = {
                "agent": event.agent,
                "contains": event.contains or [],
                "ts": idx,
            }
        if event.contains:
            for field_name in event.contains:
                v[pred_key("contains", field_name)] = True

    # ── data_read ──────────────────────────────────────────────
    elif event.event_type == "data_read" and not event.key:
        warnings.warn(
            f"Event {idx}: data_read with missing key (agent={event.agent!r}). "
            "flow() predicates will not fire.",
            stacklevel=2,
        )
    elif event.event_type == "data_read" and event.key:
        if event.key in state.data_stores:
            writer = state.data_stores[event.key]
            if writer["agent"] != event.agent:
                flow_pair = (writer["agent"], event.agent)
                state.flow_pairs.add(flow_pair)
                v[pred_key("flow", writer["agent"], event.agent)] = True

    # ── message ────────────────────────────────────────────────
    elif event.event_type == "message":
        if event.to:
            v[pred_key("flow", event.agent, event.to)] = True

    # ── context_update ─────────────────────────────────────────
    # Merge user-pushed facts into the persistent ``current_ctx`` so
    # every subsequent event sees them as ``ctx(k, v)`` atoms. The
    # update is applied *before* the ctx-emission loop below so a
    # contract at the same timestep as the update already observes
    # the new keys — matches user intuition ("set the caller id, then
    # the next tool call is attributed to it"). Non-string values are
    # stringified so atom keys stay hashable.
    elif event.event_type == "context_update":
        if event.args:
            for k, val in event.args.items():
                if k is None:
                    continue
                state.current_ctx[str(k)] = str(val) if val is not None else ""

    # ── permissions (static, from Agent model) ─────────────────
    if agents:
        agent_obj = agents.get(event.agent)
        if agent_obj and hasattr(agent_obj, "permissions"):
            for p in agent_obj.permissions:
                v[pred_key("perm", p)] = True

    # ── ctx(k, v) / ctx_matches(k, pattern) ────────────────────
    # Emit one atom per current_ctx entry at every event — these are
    # the "external facts" that ``observe_context`` pushed in. A
    # contract ``G(called(wire) → ctx(caller_id, "alice"))`` then
    # fires whenever the current caller_id matches on a wire call.
    if state.current_ctx:
        for k, val in state.current_ctx.items():
            v[pred_key("ctx", k, val)] = True
    # ctx_matches uses content_atoms extraction — we only evaluate
    # the (key, pattern) tuples the formula actually uses so we're
    # not pre-compiling every possible regex.
    if content_atoms and "ctx_matches" in content_atoms:
        for args_tuple in content_atoms["ctx_matches"]:
            if len(args_tuple) >= 2:
                key, pattern = args_tuple[0], args_tuple[1]
                cur_val = state.current_ctx.get(key)
                if cur_val is not None:
                    matched = bool(re.search(pattern, cur_val))
                else:
                    matched = False
                v[pred_key("ctx_matches", *args_tuple)] = matched

    # ── Layer 2 atoms ────────────────────────────────────────────

    # L2.1: token_count — accumulate token usage from event metadata
    # Source: event.args["tokens"] (from OTEL gen_ai.usage.* attributes)
    if event.args:
        tokens = event.args.get("tokens") or event.args.get("total_tokens")
        if tokens is not None:
            try:
                tokens = int(tokens)
            except (ValueError, TypeError):
                tokens = 0
            state.token_count["total"] = state.token_count.get("total", 0) + tokens
            # Also track by type if available
            for key in ("input_tokens", "output_tokens"):
                val = event.args.get(key)
                if val is not None:
                    try:
                        state.token_count[key] = state.token_count.get(key, 0) + int(
                            val
                        )
                    except (ValueError, TypeError):
                        pass

    # L2.4: delegation_depth — track from flow events (agent-to-agent)
    if event.event_type == "message" and event.to:
        state.delegation_depth += 1
    v[pred_key("delegation_depth")] = state.delegation_depth

    # L2.5: current_agent — which agent is acting at this timestep
    v[pred_key("current_agent", event.agent)] = True

    # ── cumulative counts (snapshot at this timestep) ──────────
    for tool, cnt in state.call_counts.items():
        v[pred_key("count", tool)] = cnt
    for (tool, pattern), cnt in state.call_with_counts.items():
        v[pred_key("count_with", tool, pattern)] = cnt

    # ── Layer 2 accumulator snapshots ─────────────────────────
    for token_type, token_val in state.token_count.items():
        v[pred_key("token_count", token_type)] = token_val

    # ── consecutive_count snapshots ──────────────────────────
    for tool, cnt in state.consecutive_counts.items():
        v[pred_key("consecutive_count", tool)] = cnt

    # ── flow / contains forward-propagation ────────────────────
    # First capture any new flows / contains predicates this event
    # added, then write *all* active ones into this valuation —
    # both stay true from their introduction onwards.  This makes
    # ``no_data_leak`` (``G(contains(x) -> !flow(x, ext))``) actually
    # fire when the write at t=N is followed by an exfil at t=M>N.
    for key, val in list(v.items()):
        if val:
            if key.startswith("flow("):
                state.active_flows.add(key)
            elif key.startswith("contains("):
                state.active_contains.add(key)
    for fk in state.active_flows:
        v[fk] = True
    for ck in state.active_contains:
        v[ck] = True

    # ── last_ts bookkeeping (fresh-only False→True transitions) ─
    # Compare against ``true_at_prev`` (snapshotted at the end of the
    # previous event). A predicate that was True last event and still
    # True now is "sustained" — we do NOT refresh its last_ts.
    # Sustained covers: forward-propagated flows / contains, and
    # ctx(k, v) atoms re-emitted from ``current_ctx`` while the fact
    # remains in scope. This is what makes
    # ``time_since(ctx(approval.role, alice))`` measure time since
    # the approval was granted, not the trivial 0 from re-emission.
    true_now: set[str] = set()
    for key, val in v.items():
        if isinstance(val, bool) and val:
            true_now.add(key)
            if key not in state.true_at_prev:
                state.last_ts[key] = state.now
    state.true_at_prev = true_now

    # ── time atoms (emitted last so they see fresh state.now) ──
    # ``now`` is unparameterized so ``Var("now").key()`` returns the
    # bare name "now" (see Var.key); we match that here rather than
    # using pred_key which would produce "now()". The two routes
    # would silently diverge otherwise — exactly the pred_key drift
    # this module's docstring warns about.
    v["now"] = state.now
    if content_atoms and "time_since" in content_atoms:
        for args_tuple in content_atoms["time_since"]:
            if not args_tuple:
                continue
            target_key = args_tuple[0]
            if target_key in state.last_ts:
                delta = state.now - state.last_ts[target_key]
            else:
                # Sentinel: predicate has never been true. "Very long
                # ago" is the right semantics for ``Le(time_since(P), N)``
                # — if P never happened, "P happened within last N
                # seconds" must evaluate False. Defaulting to 0
                # (counter-style) would invert the meaning.
                delta = 1e18
            v[pred_key("time_since", *args_tuple)] = delta

    return v


def ground(
    trace: Trace,
    agents: dict[str, object] | None = None,
    content_atoms: dict[str, set[tuple[str, ...]]] | None = None,
) -> list[dict[str, bool | int]]:
    """Converts a raw trace into per-timestep predicate valuations.

    This is the "signal extraction" phase: for each event in the trace,
    determine which atomic predicates (``called``, ``count``,
    ``contains``, ``flow``, ``perm``, ``arg_has``, ``arg_paths_within``,
    etc.) are true.

    This is the **batch** entry point — it always starts from a fresh
    :class:`GroundingState` and grounds every event. For incremental
    grounding across many calls, use :class:`GroundingState` +
    :func:`ground_event` directly (or, more commonly, the
    :class:`sponsio.runtime.verifier.Verifier` wrapper which handles the
    bookkeeping for you).

    Args:
        trace: The execution trace to ground.
        agents: Optional mapping of ``agent_id`` to ``Agent`` objects,
            used for permission predicate lookups.
        content_atoms: Optional dict from ``collect_content_atoms()``
            mapping predicate names to sets of arg tuples. When provided,
            grounding performs regex matching against event content and
            populates the corresponding predicate keys.

    Returns:
        A list of dicts, one per timestep, mapping predicate key strings
        (e.g. ``"called(fraud_check)"``) to ``True``.
    """
    state = GroundingState()
    return [
        ground_event(event, i, state, content_atoms=content_atoms, agents=agents)
        for i, event in enumerate(trace.events)
    ]
