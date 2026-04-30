"""Stdin-based hook adapter for plugin systems (Claude Code, OpenClaw, …).

The shipped runtime adapters (`sponsio.langgraph`, `sponsio.claude_agent`, …)
wrap an in-process agent. Plugin systems are different: they invoke a
shell command on every tool call and pipe a JSON event over stdin.
This module converts that protocol into a single :func:`guard_before`
call against a per-plugin contract library, then writes back the
plugin system's expected JSON / exit-code reply.

Per-plugin routing model:

    ~/.sponsio/plugins/<plugin>/sponsio.yaml   # one contract library per plugin
    ~/.sponsio/plugins/_host/sponsio.yaml      # host built-ins (Bash, Edit, …)

The ``plugin`` segment is derived from the inbound ``tool_name``:

    "Bash"                  -> "_host"          (Claude Code built-in)
    "Edit", "Write", "Read" -> "_host"
    "acme:fetch_data"       -> "acme"           (Claude Code namespaced skill)
    "mcp__acme__fetch"      -> "acme"           (MCP server convention)

Trace continuity is implemented via a per-plugin append-only JSONL log
co-located with the library file at
``~/.sponsio/plugins/<plugin>/.shield-trace.jsonl``.  Each invocation
loads prior events into the BaseGuard's monitor before evaluation, so
trace-aware contracts (must_precede, cooldown, A/G temporal patterns)
fire correctly.  After a guard call is *allowed*, the new event is
appended to the log; blocked calls are not appended because the action
never executed.

The trace log is rotated automatically: files older than
``SPONSIO_SHIELD_TRACE_TTL_HOURS`` (default 24) are pruned on access,
and the current trace is reset between rotations so a long-lived
``_host`` plugin doesn't accumulate stale events forever.  This is the
file-based "session state" — a true daemon mode (single long-running
process holding state in memory) is a future optimisation.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Built-in tool names that count as "host" tools (no plugin prefix).
# Claude Code's first-party tools live here.
_HOST_TOOL_NAMES = frozenset(
    [
        "Bash",
        "BashOutput",
        "Edit",
        "MultiEdit",
        "Glob",
        "Grep",
        "KillShell",
        "NotebookEdit",
        "Read",
        "Task",
        "TodoWrite",
        "Write",
        "WebFetch",
        "WebSearch",
        "ExitPlanMode",
    ]
)


@dataclass
class GuardOutcome:
    """Decision returned to the plugin runtime after evaluation."""

    allowed: bool
    reason: str = ""
    plugin_id: str = ""
    library_path: str | None = None


def _bucket_for_host(host: str | None, is_subagent: bool) -> str:
    """Per-host main / sub-agent bucket name.

    Each host gets its own ``_host_<host>`` directory under
    ``~/.sponsio/plugins/`` so per-IDE rules (Cursor's
    ``beforeSubmitPrompt`` checks, Claude Code's Task subagent
    boundary, OpenClaw's exec/write canonical names) live separately.
    Common rules pull from the shared ``sponsio:capability/*`` packs
    via ``include:`` in each bucket's ``sponsio.yaml`` — there's no
    single "share everything" library that all hosts read.

    Subagent buckets are namespaced per host too —
    ``_host_cursor_subagent`` / ``_host_claude_code_subagent`` —
    because Cursor's Task-spawned agents and Claude Code's Task tool
    have different surfaces. OpenClaw doesn't expose a subagent
    equivalent today; it returns ``_host_openclaw`` regardless.

    When ``host`` is ``None`` we treat the call as the legacy entry
    point and route to the original shared ``_host`` /
    ``_host_subagent`` buckets. That keeps the historical Claude Code
    integration (which doesn't yet tag its events with a host string)
    working against the existing on-disk libraries. Once the Claude
    Code hook handler starts emitting ``host="claude-code"``, those
    calls will migrate to the dedicated bucket without an opt-in
    flag.
    """
    if host == "openclaw":
        return "_host_openclaw"
    if host == "cursor":
        return "_host_cursor_subagent" if is_subagent else "_host_cursor"
    if host == "claude-code":
        return "_host_claude_code_subagent" if is_subagent else "_host_claude_code"
    # Legacy entry point (no host tag) — preserve the original
    # routing so existing libraries on disk stay reachable without
    # a manual move.
    return "_host_subagent" if is_subagent else "_host"


def derive_plugin_id(
    tool_name: str,
    host: str | None = None,
    is_subagent: bool = False,
) -> str:
    """Map a plugin-system tool name to the contract-library directory.

    Recognised forms:

    * Claude Code built-ins (``Bash``, ``Edit``, …) → ``_host``.
    * Namespaced plugin skills (``my-plugin:hello``) → ``my-plugin``.
    * MCP servers (``mcp__acme__fetch``) → ``acme``.

    The ``host`` argument is taken from the hook payload's ``"host"``
    field (``"claude-code"`` / ``"openclaw"`` / ``None``).  When it
    indicates OpenClaw, fallback (i.e. anything not matching a
    namespace pattern above) routes to ``_host_openclaw`` instead of
    ``_host``, because the OpenClaw default library uses canonical
    OpenClaw tool names (``exec`` / ``read`` / ``write`` / …) rather
    than the Claude-Code-shaped names baked into ``_host``.

    The ``is_subagent`` argument signals the call originated from a
    Task-spawned sub-agent (Claude Code's PreToolUse payload includes
    an ``agent_id`` field only when the hook fires inside a
    sub-agent).  Sub-agent calls route to ``_host_subagent`` instead
    of ``_host`` — sub-agents lack the user-conversation context the
    main agent has, so the privilege-boundary library applies tighter
    rules (e.g. no ``git commit/push``, restricted Bash whitelist).
    Tighter than ``_host``, not orthogonal — operators include the
    same packs in both libraries plus ``capability/subagent`` in the
    sub-agent variant.

    Anything else falls back to ``_host`` / ``_host_openclaw`` /
    ``_host_subagent`` so a misnamed tool still gets *some* coverage
    (default-deny would be hostile in observe mode).
    """
    fallback = _bucket_for_host(host, is_subagent)
    if not tool_name:
        return fallback
    if tool_name in _HOST_TOOL_NAMES:
        # First-party host tools (Bash / Read / Write / Edit / …)
        # route to the per-host main or sub-agent bucket. Cursor's
        # Shell tool is renamed to "Bash" upstream in
        # ``cursor_event_to_sponsio_event`` so it lands here too.
        return fallback
    if tool_name.startswith("mcp__"):
        # mcp__<server>__<tool>  (Claude Code MCP tool naming)
        parts = tool_name.split("__", 2)
        if len(parts) >= 2 and parts[1]:
            return parts[1]
        return fallback
    if host == "openclaw" and "__" in tool_name:
        # OpenClaw MCP tool naming: <server>__<tool>  (no leading
        # ``mcp__`` prefix).  Confirmed against
        # ``pi-bundle-mcp-tools.js::buildSafeToolName`` in the
        # 2026.4.14 image: ``<safeServerName>__<originalToolName>``.
        # Route the same way Claude Code's ``mcp__`` form does so
        # one set of per-plugin libraries works on both runtimes.
        server, _, _rest = tool_name.partition("__")
        if server:
            return server
        return fallback
    if ":" in tool_name:
        # <plugin>:<tool> (Claude Code namespaced skill)
        plugin, _, _rest = tool_name.partition(":")
        if plugin:
            return plugin
    return fallback


def library_root() -> Path:
    """Return the per-plugin library root directory.

    Override with ``$SPONSIO_PLUGIN_ROOT`` for tests or custom layouts.
    """
    override = os.environ.get("SPONSIO_PLUGIN_ROOT")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".sponsio" / "plugins"


def library_path_for(plugin_id: str) -> Path:
    """Path to the per-plugin ``sponsio.yaml`` library file."""
    return library_root() / plugin_id / "sponsio.yaml"


def _resolve_library(plugin_id: str) -> tuple[Path, str]:
    """Resolve ``(library_path, effective_agent_id)`` with legacy fallback.

    Cursor and Claude Code now route to per-host buckets
    (``_host_cursor`` / ``_host_claude_code``) so each IDE can carry
    its own rules. Existing users still have a populated legacy
    ``_host/sponsio.yaml`` from earlier installs — fall back to that
    when the host-specific library doesn't exist yet, so no manual
    migration is required for the runtime to keep enforcing.

    Both the path and the agent id need to fall back together: the
    legacy library declares ``agents: _host:`` (resp. ``_host_subagent``)
    as its top-level key, so :class:`BaseGuard` must be invoked with
    that same id or it cannot find the agent to evaluate against.
    Trace bucketing keeps using the new per-host plugin_id (so
    ``rate_limit`` stays per-IDE), but the contract evaluation
    transparently borrows the legacy yaml.

    The fallback only fires for the per-host main / sub-agent buckets.
    Per-MCP-server buckets (``github``, ``filesystem``, …) and the
    OpenClaw bucket return their primary path unchanged — they have
    no legacy counterpart to fall back to.
    """
    primary = library_path_for(plugin_id)
    if primary.exists():
        return primary, plugin_id

    legacy: str | None = None
    if plugin_id in {"_host_cursor", "_host_claude_code"}:
        legacy = "_host"
    elif plugin_id in {"_host_cursor_subagent", "_host_claude_code_subagent"}:
        legacy = "_host_subagent"

    if legacy:
        legacy_path = library_path_for(legacy)
        if legacy_path.exists():
            return legacy_path, legacy

    return primary, plugin_id  # primary may not exist; caller short-circuits


# ----------------------------------------------------------------------
# Per-plugin trace continuity (file-based)
# ----------------------------------------------------------------------


def _safe_conv_id(conversation_id: str) -> str:
    """Sanitize a host conversation id for use in a trace filename.

    Cursor / Claude Code emit UUID-shaped ids today, but be defensive
    against future shape drift: replace anything outside ``A-Za-z0-9._-``
    with ``_`` and cap length so a pathological host can't blow past
    the OS path limit. Empty/whitespace input falls back to "default"
    so the per-conversation bucket still exists (rather than silently
    routing to the legacy single-file path).
    """
    import re

    stripped = conversation_id.strip()
    if not stripped:
        return "default"
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", stripped)
    return safe[:64] or "default"


def _trace_file_for(plugin_id: str, conversation_id: str | None = None) -> Path:
    """Per-plugin shield trace log path.

    The trace lives **inside** each plugin's library directory at
    ``<plugin_root>/<plugin>/.shield-trace.jsonl``.  Co-locating with the
    library file means:

    1. ``$SPONSIO_PLUGIN_ROOT`` isolation is automatically inherited —
       tests that swap plugin root get a fresh trace dir for free, no
       second env var to remember.
    2. The trace and the rules that govern it travel together — moving
       a plugin library directory takes its session state with it.

    Override the trace location entirely via ``$SPONSIO_SHIELD_TRACE_ROOT``
    when you need decoupled storage (e.g. read-only library mounts).

    When ``conversation_id`` is given (Cursor / Claude Code surface one
    per IDE conversation), the trace is bucketed per-conversation:
    ``<plugin>/conv-<safe_id>.shield-trace.jsonl``. Without bucketing,
    cumulative contracts like ``rate_limit(Bash, 50)`` would compound
    Bash counts across every conversation that ever touched this plugin
    — well past the 50-call session budget the policy actually intends.
    Per-conversation bucketing is the design fix; the legacy single-file
    path is preserved for callers without a conv_id (one-off CLI tools,
    older integrations) and for backward compatibility.
    """
    override = os.environ.get("SPONSIO_SHIELD_TRACE_ROOT")
    if override:
        base = Path(override).expanduser() / plugin_id
        legacy_name = "trace.jsonl"  # historical override-mode filename
    else:
        base = library_root() / plugin_id
        legacy_name = ".shield-trace.jsonl"

    if conversation_id:
        return base / f"conv-{_safe_conv_id(conversation_id)}.{legacy_name.lstrip('.')}"
    return base / legacy_name


def _trace_ttl_seconds() -> int:
    """Stale-trace cutoff.  Default 24h; override via env for tests."""
    raw = os.environ.get("SPONSIO_SHIELD_TRACE_TTL_HOURS", "24")
    try:
        hours = float(raw)
    except ValueError:
        hours = 24.0
    return int(hours * 3600)


def _maybe_rotate(path: Path) -> None:
    """Drop the trace file if it's older than the TTL.

    A long-lived `_host` plugin (i.e. the user's whole Claude Code
    session) will accumulate events indefinitely otherwise.  We treat
    inactivity > TTL as "new session".
    """
    if not path.exists():
        return
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return
    if time.time() - mtime > _trace_ttl_seconds():
        try:
            path.unlink()
        except OSError:  # pragma: no cover - racy unlink
            pass


def _trace_root() -> Path:
    """Where namespace trace dirs live.  Mirrors the path-resolution
    logic inside :func:`_trace_file_for` so the iteration in
    :func:`_load_prior_events` reaches the same files.
    """
    override = os.environ.get("SPONSIO_SHIELD_TRACE_ROOT")
    if override:
        return Path(override).expanduser()
    return library_root()


def _read_events_from(path: Path):
    """Parse a single ``.shield-trace.jsonl`` file into ``Event`` objects.

    One bad line never poisons the rest of the trace — corrupt records
    are skipped silently.
    """
    from sponsio.models.trace import Event

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        sys.stderr.write(f"sponsio shield guard:could not read trace log {path}: {e}\n")
        return []

    events = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            # One bad line shouldn't poison the rest; skip and continue.
            continue
        try:
            events.append(
                Event(
                    ts=int(d["ts"]),
                    agent=d["agent"],
                    event_type=d["type"],
                    tool=d.get("tool"),
                    key=d.get("key"),
                    contains=d.get("contains"),
                    to=d.get("to"),
                    args=d.get("args"),
                    content=d.get("content"),
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    return events


def _load_prior_events(plugin_id: str, conversation_id: str | None = None):
    """Reconstruct the prior session trace, merged across all active
    namespaces.

    Trace files are *written* per-namespace (each tool call lands in
    the bucket :func:`derive_plugin_id` selects), but cross-namespace
    temporal contracts — e.g. "after a host ``read /work/notes/*``,
    deny public ``mcp__gh_gist__create_gist``" — need a unified view
    of session history.  Lethal-trifecta-class flows are inherently
    cross-component (untrusted input crosses tool-provider boundaries
    on its way out), so the read-side merges where the write-side
    isolates.

    Per-namespace TTL still applies independently: a stale namespace's
    file is rotated on this read just as it would be on a same-namespace
    read.  Namespaces ending in ``.bak`` / ``.disabled`` (the convention
    for retiring a contract bucket) are skipped, as are dot-prefixed
    dirs.

    ``conversation_id``-bucketed events stay isolated to their bucket
    via :func:`_trace_file_for` — the merge happens across namespaces,
    not across conversations.

    Returns a list of ``Event`` objects sorted by ``ts``, oldest first.
    Ties broken by ``agent`` (namespace name) for deterministic test
    fixtures.

    ``plugin_id`` is retained in the signature for caller compatibility
    and so the calling namespace's TTL gets noticed if its dir is the
    only activity; it does not narrow the read.
    """
    root = _trace_root()
    if not root.exists():
        return []

    try:
        ns_dirs = sorted(root.iterdir(), key=lambda p: p.name)
    except OSError:
        return []

    events: list = []
    seen_calling = False
    for ns_dir in ns_dirs:
        if not ns_dir.is_dir():
            continue
        # Skip dot-prefixed and retired (.bak / .disabled) namespaces.
        if ns_dir.name.startswith("."):
            continue
        if ns_dir.name.endswith(".bak") or ns_dir.name.endswith(".disabled"):
            continue
        path = _trace_file_for(ns_dir.name, conversation_id=conversation_id)
        _maybe_rotate(path)
        if ns_dir.name == plugin_id:
            seen_calling = True
        if not path.exists():
            continue
        events.extend(_read_events_from(path))

    # If the caller's own namespace dir doesn't exist yet (very first
    # tool call into this namespace), still rotate-probe its path so
    # callers depending on TTL side-effects keep working.  Reading from
    # a non-existent file is a no-op.
    if not seen_calling:
        own_path = _trace_file_for(plugin_id, conversation_id=conversation_id)
        _maybe_rotate(own_path)
        if own_path.exists():
            events.extend(_read_events_from(own_path))
            events.sort(key=lambda e: (e.ts, e.agent or ""))
            return events

    events.sort(key=lambda e: (e.ts, e.agent or ""))
    return events


def _append_event(
    plugin_id: str, event_dict: dict, conversation_id: str | None = None
) -> None:
    """Append one Event-shaped dict to the per-plugin trace log.

    Atomic at the level of one ``write`` syscall (a JSONL line is one
    write); concurrent hooks won't tear individual records, though
    interleaving order isn't guaranteed.  Claude Code's hook protocol
    is sequential per-session today, so this is acceptable.

    ``conversation_id`` selects the per-conversation bucket; see
    :func:`_trace_file_for`.
    """
    path = _trace_file_for(plugin_id, conversation_id=conversation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event_dict, separators=(",", ":")) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)


def evaluate_event(event: dict) -> GuardOutcome:
    """Run one PreToolUse event against the matching per-plugin library.

    Returns a :class:`GuardOutcome` describing the decision. No I/O on
    stdin/stdout — the caller is responsible for emitting the
    plugin-system-specific reply (see :func:`run_stdin`).
    """
    tool_name = event.get("tool_name") or ""
    tool_input = event.get("tool_input") or {}
    host = event.get("host") if isinstance(event.get("host"), str) else None
    # OpenClaw names MCP tools ``<server>__<tool>`` (verified against
    # ``pi-bundle-mcp-tools.js::buildSafeToolName`` in the 2026.4.14
    # image).  Claude Code uses ``mcp__<server>__<tool>``.  Normalise
    # to the Claude-Code shape so contract rules and bundled packs
    # like ``sponsio:incident/mcp-composition`` (which all reference
    # ``mcp__server__tool``) work verbatim on both runtimes.
    #
    # The "__" check excludes OpenClaw built-in names like ``read`` /
    # ``write`` / ``exec`` (single-token, no separator) which should
    # keep their canonical form and route via the ``_host_openclaw``
    # fallback.
    if (
        host == "openclaw"
        and tool_name
        and "__" in tool_name
        and not tool_name.startswith("mcp__")
    ):
        tool_name = f"mcp__{tool_name}"
    # Claude Code's PreToolUse payload includes ``agent_id`` only when
    # the hook fires inside a Task-spawned sub-agent.  Its presence
    # alone is the signal — value is just the sub-agent's ID.  Empty
    # string is treated as absent (defensive against future shape
    # drift).
    raw_agent_id = event.get("agent_id")
    is_subagent = isinstance(raw_agent_id, str) and bool(raw_agent_id.strip())
    plugin_id = derive_plugin_id(tool_name, host=host, is_subagent=is_subagent)
    # ``effective_agent_id`` may differ from ``plugin_id`` when the
    # per-host library (``_host_cursor.yaml`` / ``_host_claude_code.yaml``)
    # isn't installed yet and we fall back to the legacy ``_host``
    # library — the legacy yaml's top-level agent key is ``_host``,
    # so BaseGuard must be invoked with that id or it can't locate
    # the agent. Trace persistence keeps using ``plugin_id`` so
    # per-host bucketing is preserved.
    lib_path, effective_agent_id = _resolve_library(plugin_id)

    if not lib_path.exists():
        # No library configured for this plugin — vacuously allow.
        # Mode A's design: the absence of rules means "we haven't
        # opined on this plugin yet", not "block everything".
        return GuardOutcome(
            allowed=True,
            reason="no contract library configured",
            plugin_id=plugin_id,
            library_path=None,
        )

    # Lazy import — keeps cold-start cheap when no library exists.
    from sponsio.integrations.base import BaseGuard

    # BaseGuard prints a contract-load banner on construction and a
    # live reporter banner on each event regardless of ``verbose``.
    # Claude Code's hook protocol expects either nothing or the deny
    # JSON on stdout — anything else corrupts the channel. Capture
    # stdout for the entire eval and discard it.
    captured_out = io.StringIO()
    captured_err = io.StringIO()

    # Host-plugin runtime enforces by default — without enforce mode
    # the plugin logs but doesn't block, defeating the whole purpose.
    # Mode precedence: SPONSIO_GUARD_MODE > SPONSIO_MODE > "enforce".
    # SPONSIO_GUARD_MODE is the plugin-specific dial so operators can
    # run a session-wide SPONSIO_MODE=observe (e.g. for an unrelated
    # integration in the same process) while still enforcing here.
    guard_mode = (
        os.environ.get("SPONSIO_GUARD_MODE")
        or os.environ.get("SPONSIO_MODE")
        or "enforce"
    )
    # BaseGuard._resolve_mode reads SPONSIO_MODE FIRST and ignores its
    # ``mode=`` arg if env is set. Override the env for the scope of
    # this call so our chosen mode actually wins.
    saved_env = os.environ.get("SPONSIO_MODE")
    os.environ["SPONSIO_MODE"] = guard_mode

    # Per-conversation trace bucketing. Cursor and Claude Code both
    # emit a ``conversation_id`` per IDE conversation; routing the
    # trace file by that id keeps cumulative contracts (rate_limit /
    # idempotent / count-based) honest — without it, every Bash call
    # in the user's history compounds across conversations and
    # ``rate_limit(Bash, 50)`` blows past its session budget within
    # the first day. Other temporal contracts (must_precede /
    # cooldown / R2 prod-read-only) still see the full conversation
    # history because that's where they need to be coherent.
    conversation_id = event.get("conversation_id")
    if not isinstance(conversation_id, str):
        conversation_id = None

    # Load prior session trace from the per-plugin JSONL log (if any).
    # ``_load_prior_events`` handles TTL rotation internally.
    prior_events = _load_prior_events(plugin_id, conversation_id=conversation_id)

    try:
        with (
            contextlib.redirect_stdout(captured_out),
            contextlib.redirect_stderr(captured_err),
        ):
            guard = BaseGuard(
                agent_id=effective_agent_id,
                config=str(lib_path),
                verbose=False,
                verbosity=0,
                mode=guard_mode,
            )
            # Seed the monitor with the reconstructed trace so trace-aware
            # contracts (must_precede, cooldown, A→G temporal, …) see the
            # full session history rather than an empty trace.
            if prior_events:
                from sponsio.models.trace import Trace

                guard._monitor.import_trace(Trace(events=list(prior_events)))
            result = guard.guard_before(tool_name=tool_name, args=tool_input)
    except Exception as e:  # pragma: no cover - surfaced via stderr
        sys.stderr.write(f"sponsio plugin guard:evaluation error in {lib_path}: {e}\n")
        # Fail open — never wedge a tool call on a Sponsio bug.
        return GuardOutcome(
            allowed=True,
            reason=f"evaluation error: {e}",
            plugin_id=plugin_id,
            library_path=str(lib_path),
        )
    finally:
        # Restore SPONSIO_MODE so subsequent in-process calls (rare;
        # the CLI normally exits) don't see the override.
        if saved_env is None:
            os.environ.pop("SPONSIO_MODE", None)
        else:
            os.environ["SPONSIO_MODE"] = saved_env
    if result.allowed:
        # Append the now-permitted event to the trace log so the next
        # PreToolUse subprocess sees it.  Use the ts BaseGuard actually
        # assigned (== ``len(trace.events) - 1`` after the append).  If
        # we computed ts ourselves we'd race the monitor's own ts and
        # collisions would silently confuse re-grounded valuations on
        # the next subprocess.
        last_event = guard._monitor.trace.events[-1]
        try:
            _append_event(
                plugin_id,
                {
                    "ts": last_event.ts,
                    "agent": last_event.agent,
                    "type": last_event.event_type,
                    "tool": last_event.tool,
                    "args": last_event.args,
                },
                conversation_id=conversation_id,
            )
        except OSError as e:  # pragma: no cover - log-write failure
            sys.stderr.write(
                f"sponsio shield guard:could not append trace event: {e}\n"
            )
        return GuardOutcome(
            allowed=True,
            plugin_id=plugin_id,
            library_path=str(lib_path),
        )

    # Concatenate violation messages for the deny reason. The plugin
    # runtime shows this string back to the model, so we want a
    # compact rule-shaped explanation rather than a stack trace.
    # ``CheckResult.det_violations`` items expose ``.message`` (the
    # rendered "BLOCKED: agent.tool — …" line built by the monitor).
    reasons = []
    for v in result.det_violations:
        if getattr(v, "action", None) == "blocked":
            msg = getattr(v, "message", "") or ""
            # Strip the redundant ``BLOCKED: …`` prefix Claude Code's
            # UI will render the deny separately.
            if ":" in msg:
                msg = msg.split(":", 1)[1].strip()
            reasons.append(msg or "policy violation")
    reason = "; ".join(reasons) or "blocked by Sponsio contract"
    return GuardOutcome(
        allowed=False,
        reason=reason,
        plugin_id=plugin_id,
        library_path=str(lib_path),
    )


def render_reply(event: dict, outcome: GuardOutcome) -> tuple[str, int]:
    """Convert the outcome into the plugin runtime's reply protocol.

    Returns ``(stdout_payload, exit_code)``. Currently only the Claude
    Code protocol is supported; OpenClaw uses an in-process TS handler
    so doesn't go through this CLI.

    Reference: https://code.claude.com/docs/en/hooks
    """
    hook_event = event.get("hook_event_name") or "PreToolUse"

    if outcome.allowed:
        # Claude Code: exit 0 with no output is the fastest "allow"
        # path (no JSON parse on the host side).
        return "", 0

    if hook_event == "PreToolUse":
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": outcome.reason,
            }
        }
        return json.dumps(payload), 0

    # PostToolUse / other events: top-level decision form.
    payload = {"decision": "block", "reason": outcome.reason}
    return json.dumps(payload), 0


def run_stdin(stdin_text: str | None = None) -> int:
    """End-to-end entry point: read stdin, evaluate, write reply.

    Returns the process exit code. Wraps every internal error so a
    Sponsio bug never blocks a tool call (we fail open). Errors go
    to stderr so the operator can see them; the caller (Claude Code)
    treats non-2 exit codes as non-blocking.
    """
    raw = stdin_text if stdin_text is not None else sys.stdin.read()
    if not raw.strip():
        # Empty stdin — no event to evaluate. Allow.
        return 0

    try:
        event = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"sponsio plugin guard:invalid JSON on stdin: {e}\n")
        return 0

    if not isinstance(event, dict):
        sys.stderr.write("sponsio plugin guard:stdin payload must be a JSON object\n")
        return 0

    try:
        outcome = evaluate_event(event)
    except Exception as e:  # pragma: no cover - surfaced via stderr
        sys.stderr.write(f"sponsio plugin guard:evaluation error: {e}\n")
        return 0

    payload, code = render_reply(event, outcome)
    if payload:
        sys.stdout.write(payload)
        sys.stdout.write("\n")
    return code
