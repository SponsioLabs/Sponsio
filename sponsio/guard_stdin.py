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
import hashlib
import io
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

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
    fail_open: bool = False
    """Allowed only because the call could not be evaluated and the
    on-error policy is ``allow``. The daemon audits every such call."""


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
    if host == "gateway":
        # The MCP gateway's own library: its ``defaults`` (e.g.
        # ``unconfigured: deny``) cover every upstream server.
        return "_gateway"
    if host == "cursor":
        return "_host_cursor_subagent" if is_subagent else "_host_cursor"
    if host == "claude-code":
        return "_host_claude_code_subagent" if is_subagent else "_host_claude_code"
    # Legacy entry point (no host tag) — preserve the original
    # routing so existing libraries on disk stay reachable without
    # a manual move.
    return "_host_subagent" if is_subagent else "_host"


def _has_mcp_prefix(tool_name: str) -> bool:
    """``mcp__server__tool`` in any case; the wire format is lower-case,
    but routing on the exact spelling let a re-cased name change bucket."""
    return tool_name[:5].casefold() == "mcp__"


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
    if _has_mcp_prefix(tool_name):
        # mcp__<server>__<tool>  (Claude Code MCP tool naming). The
        # prefix is matched without regard to case: ``MCP__finance__x``
        # used to miss this branch and land in the host bucket, where
        # the finance library never saw it.
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


GATEWAY_SERVER = "sponsio"
"""The name the MCP gateway shim is registered under in a host's MCP
config (``sponsio gateway command``)."""


def gateway_inner_name(tool_name: str) -> str:
    """``mcp__sponsio__<server>__<tool>`` -> ``mcp__<server>__<tool>``.

    A host hook sees a gateway tool under the gateway's server name. It is
    decided under the upstream's own name, against the upstream's library,
    as the gateway decides it, so a server that merely calls itself
    ``sponsio`` gets no library of its own to hide behind.
    """
    prefix = f"mcp__{GATEWAY_SERVER}__"
    if tool_name[: len(prefix)].casefold() == prefix:
        inner = tool_name[len(prefix) :]
        if "__" in inner.strip("_"):
            return f"mcp__{inner}"
    return tool_name


def library_root() -> Path:
    """Return the per-plugin library root directory.

    Override with ``$SPONSIO_PLUGIN_ROOT`` for tests or custom layouts.
    """
    override = os.environ.get("SPONSIO_PLUGIN_ROOT")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".sponsio" / "plugins"


def library_path_for(plugin_id: str, root: Path | None = None) -> Path:
    """Path to the per-plugin ``sponsio.yaml`` library file.

    ``root`` overrides the environment-derived plugin root; the daemon
    passes the root it was configured with so the client's environment
    never picks the library.
    """
    return (root if root is not None else library_root()) / plugin_id / "sponsio.yaml"


_LEGACY_FALLBACK_WARNED: set[str] = set()


def _emit_legacy_fallback_warning(plugin_id: str, legacy: str) -> None:
    """One-time stderr deprecation notice when a host call falls
    through to the legacy ``_host`` bucket.

    Visibility matters here because the historical bug class was
    "user deletes ``_host_<name>/sponsio.yaml`` to disable rules,
    runtime silently falls back to ``_host/`` and keeps enforcing".
    Loud-once warning makes the dual-yaml situation impossible to
    miss while still letting existing installs keep working.

    De-duped per ``plugin_id`` per process — repeated tool calls in
    one Claude Code session don't re-print.  The host hook
    subprocess is a fresh process per invocation, so the warning
    surfaces on each session start, which is the right cadence for
    "you should migrate" guidance.
    """
    if plugin_id in _LEGACY_FALLBACK_WARNED:
        return
    _LEGACY_FALLBACK_WARNED.add(plugin_id)
    import sys

    sys.stderr.write(
        f"[sponsio] using legacy `{legacy}` bucket for `{plugin_id}` "
        f"— per-host yaml is missing.  Consolidate with:\n"
        f"  sponsio host migrate {plugin_id.removeprefix('_host_').replace('_', '-')}\n"
    )


_NO_LIBRARY_WARNED: set[str] = set()


_DENY_WORDS = ("deny", "block", "closed")
_ALLOW_WORDS = ("allow", "open")


def _emit_no_library_warning(plugin_id: str) -> None:
    """One-time stderr notice that a tool ran with nothing checking it.

    Same cadence and reasoning as
    :func:`_emit_legacy_fallback_warning`: de-duped per namespace per
    process, so a session prints it once rather than per tool call.
    """
    if plugin_id in _NO_LIBRARY_WARNED:
        return
    _NO_LIBRARY_WARNED.add(plugin_id)
    sys.stderr.write(
        f"[sponsio] no contract library for `{plugin_id}` — its tool calls "
        f"are running UNCHECKED. Author one at `{plugin_id}/sponsio.yaml`, "
        f"or refuse unconfigured tools with `sponsio host install <host> "
        f"--strict` (or SPONSIO_UNCONFIGURED=deny).\n"
    )


def _resolve_library(plugin_id: str, root: Path | None = None) -> tuple[Path, str]:
    """Resolve ``(library_path, effective_agent_id)`` with legacy fallback.

    Cursor and Claude Code route to per-host buckets
    (``_host_cursor`` / ``_host_claude_code``) so each IDE can carry
    its own rules.  When the per-host yaml is present, it
    SUPERSEDES the legacy ``_host`` bucket — no merging, no
    layering.  When it's missing, we fall back to the legacy
    ``_host/sponsio.yaml`` AND emit a one-time deprecation
    warning so the user knows where the rules are coming from.

    The fallback exists only so existing installs keep enforcing
    after a ``pip install -U`` that bumps to per-host routing —
    new installs always go through ``sponsio host install <name>``,
    which writes ``_host_<name>/`` directly.

    Per-MCP-server buckets (``github``, ``filesystem``, …) and the
    OpenClaw bucket return their primary path unchanged — they have
    no legacy counterpart to fall back to.
    """
    primary = library_path_for(plugin_id, root)
    if primary.exists():
        return primary, plugin_id

    legacy: str | None = None
    if plugin_id in {"_host_cursor", "_host_claude_code"}:
        legacy = "_host"
    elif plugin_id in {"_host_cursor_subagent", "_host_claude_code_subagent"}:
        legacy = "_host_subagent"

    if legacy:
        legacy_path = library_path_for(legacy, root)
        if legacy_path.exists():
            _emit_legacy_fallback_warning(plugin_id, legacy)
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


def conversation_key(event: dict) -> str | None:
    """The key a hook event's history is kept under: one per session.

    Cursor sends ``conversation_id``; Claude Code and OpenClaw send
    ``session_id`` (Claude Code has no ``conversation_id`` at all, so
    before this every Claude Code session shared one history and a
    ``rate_limit`` counted across sessions). The id is prefixed with
    the host (``claude-code:<session_id>``) so two hosts that happen to
    use the same id do not share a trace. A Task sub-agent carries its
    parent's ``session_id``, so its calls stay in the parent's history,
    as they always have. ``None`` when the event carries neither: such
    events share the ``default`` history.
    """
    for field_name in ("conversation_id", "session_id"):
        raw = event.get(field_name)
        if isinstance(raw, str) and raw.strip():
            host = event.get("host")
            prefix = host.strip() if isinstance(host, str) and host.strip() else "_"
            return f"{prefix}:{raw.strip()}"
    return None


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


@contextlib.contextmanager
def _trace_lock():
    """Serialise the whole read-decide-append cycle across processes.

    Every count-based rule (``rate_limit``, ``cooldown``, ``idempotent``,
    ``bounded_retry``) is read-then-write over a file: load the prior
    events, decide, append the new one. Two hook processes interleaving
    there both read the same "2 so far" and both append a third, so a
    limit of one admitted several calls. Parallel tool-call batches and
    concurrent sub-agents do exactly that; the sequential-per-session
    assumption the old code relied on is enforced nowhere.

    One exclusive lock for the whole trace root rather than per file:
    ``_load_prior_events`` merges across namespaces, so a per-file lock
    would not cover what the decision actually reads. Hook invocations
    are short, so the contention cost is a few milliseconds.

    Degrades to a no-op where ``fcntl`` is unavailable (Windows) rather
    than failing the call; the race returns there, which is the status
    quo, and the tool still runs.
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX
        yield
        return
    root = _trace_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        handle = open(root / ".shield.lock", "a+")
    except OSError:  # pragma: no cover - unwritable root
        yield
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


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


def _hook_on_error() -> str:
    """``"deny"`` (default) or ``"allow"``: what a guard error turns into."""
    raw = os.environ.get("SPONSIO_HOOK_ON_ERROR", "").strip().lower()
    return "allow" if raw in ("allow", "open", "fail-open", "fail_open") else "deny"


# ----------------------------------------------------------------------
# The decision, separated from where rules and history come from
# ----------------------------------------------------------------------
#
# ``evaluate`` is a pure function of (event, store, traces, policy). It
# reads no file and no environment variable. The per-call hook path
# (``evaluate_event``) feeds it a file-backed store and trace store plus
# a policy resolved from the environment; the daemon feeds it a cached
# snapshot store, an in-memory trace store, and its own config. Both
# make the same decision for the same inputs, which is what lets the
# existing hook tests run unchanged through the daemon.


@dataclass(frozen=True)
class GuardPolicy:
    """Knobs that shape a decision, resolved by the caller.

    ``mode`` is ``enforce`` or ``observe``. ``on_error`` is what an
    evaluation failure becomes (``deny`` by default). ``unconfigured``
    is ``deny`` / ``allow`` for a tool with no library, or ``None`` to
    let the host library's ``defaults.unconfigured`` decide.
    """

    mode: str = "enforce"
    on_error: str = "deny"
    unconfigured: str | None = None
    trace_ttl_hours: float = 24.0

    @classmethod
    def from_env(cls) -> "GuardPolicy":
        """The legacy resolution: ``SPONSIO_GUARD_MODE`` then
        ``SPONSIO_MODE`` then ``enforce``; ``SPONSIO_HOOK_ON_ERROR``;
        ``SPONSIO_UNCONFIGURED``; ``SPONSIO_SHIELD_TRACE_TTL_HOURS``."""
        mode = (
            os.environ.get("SPONSIO_GUARD_MODE")
            or os.environ.get("SPONSIO_MODE")
            or "enforce"
        )
        raw = os.environ.get("SPONSIO_UNCONFIGURED", "").strip().lower()
        unconfigured: str | None = None
        if raw in _DENY_WORDS:
            unconfigured = "deny"
        elif raw in _ALLOW_WORDS:
            unconfigured = "allow"
        return cls(
            mode=mode,
            on_error=_hook_on_error(),
            unconfigured=unconfigured,
            trace_ttl_hours=_trace_ttl_seconds() / 3600,
        )


@dataclass(frozen=True)
class LibrarySnapshot:
    """One loaded library, as the decision sees it.

    ``agent_id`` is the effective agent key (it differs from
    ``plugin_id`` when the legacy ``_host`` bucket is serving a per-host
    request). ``content_hash`` covers the file and every included pack.
    ``error`` carries the load failure when the file exists but could
    not be parsed; ``config`` is then ``None`` and the decision follows
    the on-error policy.
    """

    plugin_id: str
    agent_id: str
    path: str
    content_hash: str
    config: Any = None
    error: BaseException | None = None
    held_reason: str | None = None
    """Set when the daemon will not serve this library: it is weaker
    than the last approved ruleset and nothing older is loaded. Every
    call to the bucket is denied with this reason, whatever on_error."""

    @property
    def defaults(self) -> dict:
        raw = getattr(self.config, "defaults", None)
        return raw if isinstance(raw, dict) else {}


class RuleStore(Protocol):
    """Where libraries come from."""

    def resolve(self, plugin_id: str) -> LibrarySnapshot | None:
        """The snapshot serving ``plugin_id``, or ``None`` when no
        library exists for it (after legacy fallback)."""
        ...


class TraceStore(Protocol):
    """Where session history lives."""

    def events(self, plugin_id: str, conversation_id: str | None) -> list:
        """Prior events of the conversation, merged across buckets,
        oldest first."""
        ...

    def append(self, plugin_id: str, conversation_id: str | None, event: dict) -> None:
        """Record one admitted event."""
        ...

    def attach_output(
        self,
        plugin_id: str,
        conversation_id: str | None,
        tool_name: str,
        text: str,
    ) -> bool:
        """Attach a tool's output to its most recent recorded call."""
        ...


class GuardCache(Protocol):
    """Live guards kept between calls (the daemon's), so a check on a long
    session adds one event instead of rebuilding the whole history.

    ``checkout`` returns a guard already synced to the conversation's
    current history, or ``None`` (build a fresh one). ``checkin`` offers
    the guard back after a decision; the cache keeps it only if it still
    matches the store. ``discard`` drops it (the decision failed, or the
    guard's trace now holds something the store does not).
    """

    def checkout(self, key: tuple, conversation_id: str | None) -> Any | None: ...

    def checkin(self, key: tuple, guard: Any, conversation_id: str | None) -> None: ...

    def discard(self, key: tuple) -> None: ...


def _content_hash(paths: list[str] | tuple[str, ...]) -> str:
    """Hash the content of every file in ``paths``, in order, prefixed
    with its path so a swap of two files does not collide."""
    h = hashlib.sha256()
    for p in paths:
        h.update(str(p).encode("utf-8"))
        h.update(b"\0")
        try:
            h.update(Path(p).read_bytes())
        except OSError:
            h.update(b"<unreadable>")
        h.update(b"\0")
    return h.hexdigest()


def load_snapshot(plugin_id: str, lib_path: Path, agent_id: str) -> LibrarySnapshot:
    """Load ``lib_path`` into a :class:`LibrarySnapshot`.

    A parse failure is captured on the snapshot rather than raised, so
    the caller can turn it into the on-error decision with the path in
    the message.
    """
    from sponsio.config import load_config

    try:
        config = load_config(str(lib_path))
    except Exception as e:  # noqa: BLE001 - every load error is a decision input
        return LibrarySnapshot(
            plugin_id=plugin_id,
            agent_id=agent_id,
            path=str(lib_path),
            content_hash=_content_hash([str(lib_path)]),
            error=e,
        )
    files = config.source_files or (str(lib_path),)
    return LibrarySnapshot(
        plugin_id=plugin_id,
        agent_id=agent_id,
        path=str(lib_path),
        content_hash=_content_hash(files),
        config=config,
    )


class FileRuleStore:
    """Reads the library from disk on every resolve.

    This is the per-call hook path's store: a fresh process has no
    cache to keep, so it parses the file each time. The daemon's
    snapshot store (``sponsio.daemon.guard_service``) caches by content.
    """

    def __init__(self, root: Path | None = None) -> None:
        self._root = root

    def resolve(self, plugin_id: str) -> LibrarySnapshot | None:
        lib_path, agent_id = _resolve_library(plugin_id, self._root)
        if not lib_path.exists():
            return None
        return load_snapshot(plugin_id, lib_path, agent_id)


class FileTraceStore:
    """The JSONL trace logs next to each library (the legacy layout)."""

    def events(self, plugin_id: str, conversation_id: str | None) -> list:
        return _load_prior_events(plugin_id, conversation_id=conversation_id)

    def append(self, plugin_id: str, conversation_id: str | None, event: dict) -> None:
        _append_event(plugin_id, event, conversation_id=conversation_id)

    def attach_output(
        self,
        plugin_id: str,
        conversation_id: str | None,
        tool_name: str,
        text: str,
    ) -> bool:
        return _attach_tool_output(plugin_id, tool_name, text, conversation_id)


def _unconfigured_denies(
    policy: GuardPolicy, store: RuleStore, host_bucket: str | None
) -> bool:
    """Whether a tool with no contract library is refused.

    ``policy.unconfigured`` wins when set. Otherwise the host's own
    library decides through ``defaults.unconfigured: deny``, which
    ``sponsio host install --strict`` writes. Keeping the posture in that
    file rather than only in the environment means it survives a new
    shell, and the agent cannot flip it: the host library is covered by
    ``capability/self-modify``.
    """
    if policy.unconfigured == "deny":
        return True
    if policy.unconfigured == "allow":
        return False
    if host_bucket is None:
        return False
    snap = store.resolve(host_bucket)
    if snap is None:
        return False
    return str(snap.defaults.get("unconfigured", "")).strip().lower() in _DENY_WORDS


def _error_outcome(
    exc: BaseException,
    plugin_id: str,
    library_path: str | None,
    on_error: str | None = None,
) -> GuardOutcome:
    """Outcome for a call the guard could not evaluate.

    Denied by default. A rulebook that fails to load (a YAML syntax
    error, a contract that does not parse) used to fall through as an
    allow, which silently switched off every rule in that library until
    someone read stderr. ``SPONSIO_HOOK_ON_ERROR=allow`` (or an
    ``on_error`` of ``allow`` from the caller's policy) restores that
    fail-open behaviour for operators who prefer availability.
    """
    where = f" in {library_path}" if library_path else ""
    sys.stderr.write(f"sponsio plugin guard:evaluation error{where}: {exc}\n")
    if (on_error if on_error is not None else _hook_on_error()) == "allow":
        return GuardOutcome(
            allowed=True,
            reason=f"evaluation error: {exc}",
            plugin_id=plugin_id,
            library_path=library_path,
            fail_open=True,
        )
    return GuardOutcome(
        allowed=False,
        reason=(
            f"Sponsio could not evaluate this call ({exc}). Fix the contract "
            "library, or set SPONSIO_HOOK_ON_ERROR=allow to fail open."
        ),
        plugin_id=plugin_id,
        library_path=library_path,
    )


def malformed_outcome(problem: str) -> GuardOutcome:
    """Outcome for hook input that is not an event: empty stdin, not
    JSON, not a JSON object, or a host Sponsio does not know.

    Denied. Such input used to be allowed as "a host protocol problem",
    so anything that could garble or truncate what reaches the hook
    turned the guard off for that call.
    """
    return GuardOutcome(
        allowed=False,
        reason=f"Sponsio could not read this hook call ({problem}), so it is denied.",
    )


def parse_hook_input(raw: str) -> tuple[dict | None, str | None]:
    """``(event, problem)`` for the text a host wrote to the hook's stdin;
    exactly one is set."""
    if not raw.strip():
        return None, "empty stdin"
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, f"invalid JSON on stdin: {e}"
    if not isinstance(obj, dict):
        return None, "stdin payload must be a JSON object"
    return obj, None


# Tool outputs attached to the trace are capped so one large file read
# cannot bloat the session log.
_MAX_ATTACHED_OUTPUT = 4000


def _attach_tool_output(
    plugin_id: str, tool_name: str, output: str, conversation_id: str | None
) -> bool:
    """Attach ``output`` to the most recent recorded call of ``tool_name``.

    Rewrites the trace file in place (temp file + rename). Returns True
    when a call was found and updated.
    """
    path = _trace_file_for(plugin_id, conversation_id=conversation_id)
    if not path.exists():
        return False
    lines = path.read_text(encoding="utf-8").splitlines()
    for i in range(len(lines) - 1, -1, -1):
        try:
            d = json.loads(lines[i])
        except ValueError:
            continue
        if (
            d.get("type") == "tool_call"
            and d.get("tool") == tool_name
            and d.get("content") is None
        ):
            d["content"] = output[:_MAX_ATTACHED_OUTPUT]
            lines[i] = json.dumps(d, separators=(",", ":"))
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            tmp.replace(path)
            return True
    return False


def _observe_post_event(
    event: dict, tool_name: str, plugin_id: str, traces: TraceStore
) -> GuardOutcome:
    """Handle a ``PostToolUse`` (or any non-pre) event: record, never gate.

    The tool output (``tool_response`` in Claude Code's payload) is
    attached to the call the ``PreToolUse`` hook admitted, so content
    atoms (``output_has``, ``contains``) can see it on the next check.
    """
    output = event.get("tool_response")
    if output is None:
        output = event.get("tool_result")
    if output is None:
        output = event.get("output")
    if output is not None and tool_name:
        text = output if isinstance(output, str) else json.dumps(output, default=str)
        conversation_id = conversation_key(event)
        try:
            traces.attach_output(plugin_id, conversation_id, tool_name, text)
        except OSError as e:  # pragma: no cover - log-write failure
            sys.stderr.write(
                f"sponsio plugin guard:could not attach tool output: {e}\n"
            )
    return GuardOutcome(
        allowed=True,
        reason="post-hoc event: output recorded, nothing to gate",
        plugin_id=plugin_id,
    )


def evaluate_event(event: dict) -> GuardOutcome:
    """Run one hook event against the matching per-plugin library.

    The legacy per-call entry point: library from disk, history from
    the JSONL logs next to it, policy from the environment. Returns a
    :class:`GuardOutcome`; the caller emits the host-specific reply
    (see :func:`run_stdin`).

    Holds :func:`_trace_lock` for the whole decision. Loading the prior
    events, deciding, and appending the new one is one critical section:
    split it across two hook processes and both read the same history,
    both decide "under the limit", and both append. Every caller gets the
    lock, not just the CLI entry point.
    """
    with _trace_lock():
        return evaluate(
            event,
            store=FileRuleStore(),
            traces=FileTraceStore(),
            policy=GuardPolicy.from_env(),
        )


def _evaluate_event_locked(event: dict) -> GuardOutcome:
    """The body of :func:`evaluate_event`, for callers that hold the lock."""
    return evaluate(
        event,
        store=FileRuleStore(),
        traces=FileTraceStore(),
        policy=GuardPolicy.from_env(),
    )


def evaluate(
    event: dict,
    *,
    store: RuleStore,
    traces: TraceStore,
    policy: GuardPolicy,
    guards: GuardCache | None = None,
    library: str | None = None,
) -> GuardOutcome:
    """Decide one hook event. Pure in its inputs: no file, no environment.

    The caller serialises calls that share a conversation (the per-call
    path holds a file lock; the daemon holds one lock per conversation),
    because the decision reads history and then appends to it.

    ``guards`` (the daemon's) reuses a live guard per (bucket, library
    content, conversation, mode) instead of building one and replaying
    the history on every call; the decision is the same either way.

    ``library`` names the library to decide against, for a caller that
    has one (the SDK's ``backend="daemon"``); otherwise it is derived
    from the tool name and host (:func:`derive_plugin_id`).
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
        and not _has_mcp_prefix(tool_name)
    ):
        tool_name = f"mcp__{tool_name}"
    # Claude Code's PreToolUse payload includes ``agent_id`` only when
    # the hook fires inside a Task-spawned sub-agent.  Its presence
    # alone is the signal — value is just the sub-agent's ID.  Empty
    # string is treated as absent (defensive against future shape
    # drift).
    raw_agent_id = event.get("agent_id")
    is_subagent = isinstance(raw_agent_id, str) and bool(raw_agent_id.strip())
    tool_name = gateway_inner_name(tool_name)
    plugin_id = library or derive_plugin_id(
        tool_name, host=host, is_subagent=is_subagent
    )
    hook_event = event.get("hook_event_name") or "PreToolUse"
    if hook_event != "PreToolUse":
        # The tool has already run. There is nothing to gate, and running
        # the pre-check here would append a second event for one
        # execution (halving every count-based budget and blocking calls
        # that already happened). Record the output instead.
        return _observe_post_event(event, tool_name, plugin_id, traces)

    snap = store.resolve(plugin_id)

    if snap is None:
        # No library configured for this plugin.
        #
        # Mode A's design: the absence of rules means "we haven't opined
        # on this plugin yet", not "block everything" — blocking every
        # unconfigured MCP server would wedge the host on install. But
        # the docs say every tool call goes through the guard, and any
        # third-party MCP server outside the few shipped examples lands
        # here, so silence read as "checked and fine" when it meant
        # "never looked". It is now said out loud, once per namespace,
        # the same way the legacy-bucket fallback is; and an operator who
        # wants the strict posture can have it.
        if _unconfigured_denies(policy, store, _bucket_for_host(host, is_subagent)):
            return GuardOutcome(
                allowed=False,
                reason=(
                    f"no contract library for `{plugin_id}`, and unconfigured "
                    f"tools are denied (SPONSIO_UNCONFIGURED=deny or "
                    f"defaults.unconfigured: deny in the host library). "
                    f"Author {plugin_id}/sponsio.yaml to allow it."
                ),
                plugin_id=plugin_id,
                library_path=None,
            )
        _emit_no_library_warning(plugin_id)
        return GuardOutcome(
            allowed=True,
            reason="no contract library configured",
            plugin_id=plugin_id,
            library_path=None,
        )

    if snap.held_reason is not None:
        return GuardOutcome(
            allowed=False,
            reason=snap.held_reason,
            plugin_id=plugin_id,
            library_path=snap.path,
        )
    if snap.error is not None:
        return _error_outcome(snap.error, plugin_id, snap.path, policy.on_error)

    # BaseGuard prints a live reporter banner on each event regardless
    # of ``verbose``. Claude Code's hook protocol expects either nothing
    # or the deny JSON on stdout — anything else corrupts the channel.
    # Capture stdout for the entire eval and discard it.
    captured_out = io.StringIO()
    captured_err = io.StringIO()

    # Per-session trace bucketing (see :func:`conversation_key`): keeps
    # cumulative contracts (rate_limit / idempotent / count-based)
    # counting one session, not the user's whole history. Ordering
    # contracts (must_precede / cooldown) see the session's full
    # history, merged across buckets.
    conversation_id = conversation_key(event)
    cache_key = (plugin_id, snap.content_hash, conversation_id, policy.mode)
    guard = None
    used_context = False

    try:
        with (
            contextlib.redirect_stdout(captured_out),
            contextlib.redirect_stderr(captured_err),
        ):
            guard = _session_guard(
                snap, plugin_id, conversation_id, policy, traces, guards
            )
            # Bridge any host-supplied context facts (caller identity,
            # tenant, signed-message metadata, …) into the contract layer
            # before the tool call is evaluated. The host plugin packs
            # these into the ``context`` field of the PreToolUse payload;
            # we forward them via ``observe_context`` so subsequent
            # ``ctx(k, v)`` / ``ctx_matches`` atoms see them at the same
            # timestep as the tool call. Non-dict values are ignored —
            # defensive against future shape drift in host hook payloads.
            context_facts = event.get("context")
            if isinstance(context_facts, dict) and context_facts:
                clean = {
                    str(k): str(v)
                    for k, v in context_facts.items()
                    if k is not None and v is not None
                }
                if clean:
                    guard.observe_context(clean)
                    used_context = True
            result = guard.guard_before(tool_name=tool_name, args=tool_input)
    except Exception as e:
        if guards:
            guards.discard(cache_key)
        return _error_outcome(e, plugin_id, snap.path, policy.on_error)
    # ``not stop_original`` rather than ``allowed``: ``allowed`` is False
    # only on ``blocked``, so a redirected verdict fell through here as a
    # permit — a fail-open hole for a hook adapter with no substitution
    # path. Redirects must deny.
    if not result.stop_original:
        # Append the now-permitted event to the trace so the next check
        # sees it.  Use the ts BaseGuard actually assigned (==
        # ``len(trace.events) - 1`` after the append).  If we computed ts
        # ourselves we'd race the monitor's own ts and collisions would
        # silently confuse re-grounded valuations on the next check.
        last_event = guard._monitor.trace.events[-1]
        try:
            traces.append(
                plugin_id,
                conversation_id,
                {
                    "ts": last_event.ts,
                    "agent": last_event.agent,
                    "type": last_event.event_type,
                    "tool": last_event.tool,
                    "args": last_event.args,
                },
            )
        except OSError as e:  # pragma: no cover - log-write failure
            sys.stderr.write(
                f"sponsio shield guard:could not append trace event: {e}\n"
            )
        _return_guard(guards, cache_key, guard, conversation_id, used_context)
        return GuardOutcome(
            allowed=True,
            plugin_id=plugin_id,
            library_path=snap.path,
        )
    _return_guard(guards, cache_key, guard, conversation_id, used_context)

    # Concatenate violation messages for the deny reason. The plugin
    # runtime shows this string back to the model, so we want a
    # compact rule-shaped explanation rather than a stack trace.
    # ``CheckResult.det_violations`` items expose ``.message`` (the
    # rendered "BLOCKED: agent.tool — …" line built by the monitor).
    from sponsio.integrations.base import is_stopping_action

    reasons = []
    for v in result.det_violations:
        # Canonical stopping set: a redirected verdict denies here too,
        # so its message must be eligible as the deny reason.
        if is_stopping_action(getattr(v, "action", "") or ""):
            reasons.append(_deny_reason(v))
    reason = "; ".join(dict.fromkeys(reasons)) or "blocked by Sponsio contract"
    return GuardOutcome(
        allowed=False,
        reason=reason,
        plugin_id=plugin_id,
        library_path=snap.path,
    )


def _session_guard(
    snap: LibrarySnapshot,
    plugin_id: str,
    conversation_id: str | None,
    policy: GuardPolicy,
    traces: TraceStore,
    guards: GuardCache | None,
) -> Any:
    """A guard for ``snap`` that has seen the conversation's history:
    the cached one when ``guards`` holds it, otherwise a new one."""
    cache_key = (plugin_id, snap.content_hash, conversation_id, policy.mode)
    guard = guards.checkout(cache_key, conversation_id) if guards else None
    if guard is not None:
        return guard
    from sponsio.integrations.base import BaseGuard

    # ``env_mode=False``: the policy's mode is the decision's mode. The
    # process environment is not consulted, so a daemon serving several
    # hosts can hold a mode per host, and a client's environment never
    # reaches the decision.
    guard = BaseGuard(
        agent_id=snap.agent_id,
        parsed_config=snap.config,
        verbose=False,
        verbosity=0,
        mode=policy.mode,
        env_mode=False,
        init_banner=False,
        auto_summary=False,
    )
    # Seed the monitor with the reconstructed trace so trace-aware
    # contracts (must_precede, cooldown, A→G temporal, …) see the full
    # session history rather than an empty trace.
    prior_events = traces.events(plugin_id, conversation_id)
    if prior_events:
        from sponsio.models.trace import Trace

        guard._monitor.import_trace(Trace(events=list(prior_events)))
    return guard


def allowed_tools(
    tool_names: list[str],
    *,
    host: str | None,
    conversation_id: str | None,
    store: RuleStore,
    traces: TraceStore,
    policy: GuardPolicy,
    guards: GuardCache | None = None,
    library: str | None = None,
) -> list[str]:
    """The subset of ``tool_names`` a call to which would be admitted now.

    The name-level question :meth:`BaseGuard.filter_tools` answers, asked
    of each tool's own library with the conversation's history, so a
    gateway can list only the tools the session's current state allows.
    Arguments are not known yet; a call is still decided by
    :func:`evaluate`. A library that is held or broken (with ``on_error:
    deny``) allows none of its tools; a tool with no library is allowed
    unless unconfigured tools are denied. In observe mode every tool is
    allowed: :meth:`BaseGuard.filter_tools` answers regardless of mode,
    and observe blocks nothing. Order is kept. Like :func:`evaluate`, the
    caller serialises calls per conversation.
    """
    if policy.mode == "observe":
        return list(tool_names)
    groups: dict[str, list[str]] = {}
    for name in tool_names:
        plugin_id = library or derive_plugin_id(name, host=host)
        groups.setdefault(plugin_id, []).append(name)
    allowed: set[str] = set()
    for plugin_id, names in groups.items():
        snap = store.resolve(plugin_id)
        if snap is None:
            if not _unconfigured_denies(policy, store, _bucket_for_host(host, False)):
                allowed.update(names)
            continue
        if snap.held_reason is not None:
            continue
        if snap.error is not None:
            if policy.on_error == "allow":
                allowed.update(names)
            continue
        cache_key = (plugin_id, snap.content_hash, conversation_id, policy.mode)
        try:
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                guard = _session_guard(
                    snap, plugin_id, conversation_id, policy, traces, guards
                )
                allowed.update(guard.filter_tools(names))
        except Exception:  # noqa: BLE001 - an unevaluable library offers nothing
            if guards:
                guards.discard(cache_key)
            if policy.on_error == "allow":
                allowed.update(names)
            continue
        _return_guard(guards, cache_key, guard, conversation_id, False)
    return [n for n in tool_names if n in allowed]


_VIOLATED = "det constraint violated: "


def _looks_like_formula(text: str) -> bool:
    return text.startswith(("G(", "F(", "X(", "(")) or "->" in text


def _deny_reason(v: Any) -> str:
    """One violation, said the way its author named the rule.

    The contract's own ``desc`` first; the formula's description after it
    in parentheses when it says something more and is not a raw formula.
    A contract without a ``desc`` is named by its pattern (``rule_id``),
    so "Bash.command must not match forbidden patterns" still says which
    rule it was.
    """
    msg = getattr(v, "message", "") or ""
    detail = msg.split(_VIOLATED, 1)[1].strip() if _VIOLATED in msg else ""
    if not detail and ":" in msg:
        detail = msg.split(":", 1)[1].strip()
    rule = (getattr(v, "contract_desc", "") or "").strip()
    if rule:
        if detail and detail != rule and not _looks_like_formula(detail):
            return f"{rule} ({detail})"
        return rule
    rule_id = (getattr(v, "rule_id", "") or "").strip()
    if rule_id and detail:
        return f"[{rule_id}] {detail}"
    return detail or rule_id or "policy violation"


def _return_guard(
    guards: GuardCache | None,
    key: tuple,
    guard: Any,
    conversation_id: str | None,
    used_context: bool,
) -> None:
    """Offer the guard back to the cache, unless its trace now holds a
    ``context_update`` event the store never records: a rebuilt guard
    would not have it, so a reused one must not either."""
    if guards is None:
        return
    if used_context:
        guards.discard(key)
    else:
        guards.checkin(key, guard, conversation_id)


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


def run_stdin(stdin_text: str | None = None, *, default_host: str | None = None) -> int:
    """End-to-end entry point: read stdin, evaluate, write reply.

    Returns the process exit code. Malformed stdin (empty, not JSON, not
    an object) is denied (:func:`malformed_outcome`). An error while
    evaluating the event follows :func:`_error_outcome`: denied unless
    ``SPONSIO_HOOK_ON_ERROR=allow``. Errors go to stderr so the operator
    can see them.

    ``default_host`` is the host the hook was installed for (``sponsio
    host guard claude-code`` knows it is Claude Code). Claude Code's own
    payload carries no ``host`` field, so without it every call routed
    to the legacy ``_host`` bucket while ``sponsio host install --strict``
    had written ``defaults.unconfigured: deny`` into ``_host_claude_code``:
    the posture was on disk and never read, and an MCP tool with no
    library still ran. A ``host`` the payload does carry wins.
    """
    raw = stdin_text if stdin_text is not None else sys.stdin.read()
    event, problem = parse_hook_input(raw)
    if event is None:
        outcome = malformed_outcome(problem or "")
        payload, _code = render_reply({}, outcome)
        sys.stdout.write(payload + "\n")
        sys.stderr.write(outcome.reason + "\n")
        # Exit 2 as well: input this broken may come with a host that
        # does not read the reply either, and exit 2 blocks regardless.
        return 2

    if default_host and not (
        isinstance(event.get("host"), str) and event["host"].strip()
    ):
        event["host"] = default_host

    try:
        outcome = evaluate_event(event)
    except Exception as e:
        outcome = _error_outcome(e, "", None)

    payload, code = render_reply(event, outcome)
    if payload:
        sys.stdout.write(payload)
        sys.stdout.write("\n")
    return code
