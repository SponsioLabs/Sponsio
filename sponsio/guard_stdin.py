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

Trace continuity across calls is not yet implemented — every invocation
gets a fresh empty trace, so trace-aware contracts (must_precede,
rate_limit, cooldown) are silent in this prototype. Argument-level
contracts (scope_limit, arg_blacklist, arg_value_range, dangerous_*,
tool_allowlist) all fire correctly because they evaluate on the
single current event. A daemon mode that maintains per-session traces
is the next iteration.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
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


def derive_plugin_id(tool_name: str) -> str:
    """Map a plugin-system tool name to the contract-library directory.

    Recognised forms:

    * Claude Code built-ins (``Bash``, ``Edit``, …) → ``_host``.
    * Namespaced plugin skills (``my-plugin:hello``) → ``my-plugin``.
    * MCP servers (``mcp__acme__fetch``) → ``acme``.

    Anything else falls back to ``_host`` so a misnamed tool still gets
    *some* coverage (default-deny would be hostile in observe mode).
    """
    if not tool_name:
        return "_host"
    if tool_name in _HOST_TOOL_NAMES:
        return "_host"
    if tool_name.startswith("mcp__"):
        # mcp__<server>__<tool>
        parts = tool_name.split("__", 2)
        if len(parts) >= 2 and parts[1]:
            return parts[1]
        return "_host"
    if ":" in tool_name:
        # <plugin>:<tool> (Claude Code namespaced skill)
        plugin, _, _rest = tool_name.partition(":")
        if plugin:
            return plugin
    return "_host"


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


def evaluate_event(event: dict) -> GuardOutcome:
    """Run one PreToolUse event against the matching per-plugin library.

    Returns a :class:`GuardOutcome` describing the decision. No I/O on
    stdin/stdout — the caller is responsible for emitting the
    plugin-system-specific reply (see :func:`run_stdin`).
    """
    tool_name = event.get("tool_name") or ""
    tool_input = event.get("tool_input") or {}
    plugin_id = derive_plugin_id(tool_name)
    lib_path = library_path_for(plugin_id)

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

    # Sponsio-shield enforces by default — without enforce mode the
    # shield logs but doesn't block, defeating the whole purpose.
    # Mode precedence: SPONSIO_GUARD_MODE > SPONSIO_MODE > "enforce".
    # SPONSIO_GUARD_MODE is the shield-specific dial so operators can
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

    try:
        with (
            contextlib.redirect_stdout(captured_out),
            contextlib.redirect_stderr(captured_err),
        ):
            guard = BaseGuard(
                agent_id=plugin_id,
                config=str(lib_path),
                verbose=False,
                verbosity=0,
                mode=guard_mode,
            )
            result = guard.guard_before(tool_name=tool_name, args=tool_input)
    except Exception as e:  # pragma: no cover - surfaced via stderr
        sys.stderr.write(f"sponsio shield guard:evaluation error in {lib_path}: {e}\n")
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
        sys.stderr.write(f"sponsio shield guard:invalid JSON on stdin: {e}\n")
        return 0

    if not isinstance(event, dict):
        sys.stderr.write("sponsio shield guard:stdin payload must be a JSON object\n")
        return 0

    try:
        outcome = evaluate_event(event)
    except Exception as e:  # pragma: no cover - surfaced via stderr
        sys.stderr.write(f"sponsio shield guard:evaluation error: {e}\n")
        return 0

    payload, code = render_reply(event, outcome)
    if payload:
        sys.stdout.write(payload)
        sys.stdout.write("\n")
    return code
