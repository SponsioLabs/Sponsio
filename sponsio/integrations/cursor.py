"""Cursor IDE hook adapter — translate Cursor 1.7+ hook events into
Sponsio's stdin-protocol and back.

Cursor exposes a deny-capable, command-based hook system:
``hooks.json`` declares a shell command per event; Cursor pipes a JSON
payload over stdin; the command writes a JSON decision back on stdout
and signals deny via exit code ``2``.

This module is the schema bridge between Cursor's payload shape and
:mod:`sponsio.guard_stdin`'s ``evaluate_event``.  Decision logic is
fully delegated to the existing Sponsio guard pipeline (per-plugin
contract library, trace continuity, det+sto pipelines).

Cursor events handled here:

* ``preToolUse``           — universal, fires before every tool
* ``beforeShellExecution`` — shell-specific
* ``beforeMCPExecution``   — MCP-tool-specific
* ``beforeReadFile``       — file-read access control
* ``beforeSubmitPrompt``   — pre-prompt input gate (allow-only today)
* ``postToolUse`` /
  ``afterShellExecution``  — post hooks; appended to trace, never deny

Reference: https://cursor.com/docs/hooks.md
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from sponsio.guard_stdin import GuardOutcome, evaluate_event

# ---------------------------------------------------------------------------
# Tool-name normalisation: Cursor → Sponsio canonical
# ---------------------------------------------------------------------------
#
# Cursor's preToolUse uses TitleCase tool names ("Shell", "Read",
# "Write", …) plus an "MCP:<server>__<tool>" form for MCP calls.
# Sponsio's plugin libraries — particularly the shipped ``_host``
# pack — are written against Claude-Code-shaped names ("Bash", "Edit",
# …).  Mapping Cursor → Claude-Code-shape lets a single ``_host``
# library cover both IDEs without per-tool duplication.
_CURSOR_TOOL_RENAME: dict[str, str] = {
    "Shell": "Bash",
    "Terminal": "Bash",
    # Read / Write / Edit / Grep / Task / MultiEdit / NotebookEdit /
    # WebFetch / WebSearch / TodoWrite all already match the canonical
    # Claude-Code names baked into ``_HOST_TOOL_NAMES`` — pass through.
}


def _normalise_tool_name(raw: str) -> str:
    """Map a Cursor tool-name string into the Sponsio canonical form."""
    if not raw:
        return raw
    if raw.startswith("MCP:"):
        # Cursor: "MCP:<server>__<tool>"  →  Sponsio: "mcp__<server>__<tool>"
        # When Cursor only gives "MCP:<server>" without the tool
        # segment we still produce a routable plugin id; downstream
        # ``derive_plugin_id`` strips ``mcp__`` and uses the next
        # segment as the plugin id.
        return "mcp__" + raw[len("MCP:") :]
    return _CURSOR_TOOL_RENAME.get(raw, raw)


# ---------------------------------------------------------------------------
# Payload translation
# ---------------------------------------------------------------------------


def _coerce_tool_input(raw: Any) -> dict[str, Any]:
    """Cursor sometimes sends ``tool_input`` as a JSON-encoded string
    (notably ``beforeMCPExecution``); other events send it as an object.
    Always return a dict so contract args see a uniform shape.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    return {}


# ---------------------------------------------------------------------------
# Subagent registry — track which Cursor conversation_ids are Task-spawned
# subagents so their tool calls route to the stricter ``_host_subagent``
# library, mirroring the main/subagent split Claude Code already has.
#
# Cursor's hook protocol fires ``subagentStart`` once when a Task subagent
# spawns; subsequent ``preToolUse`` events from inside the subagent carry
# its conversation_id but no other "I'm a subagent" signal.  We persist a
# tiny JSONL registry between hook subprocesses so the lookup is reliable.
# ---------------------------------------------------------------------------


def _subagent_registry_path() -> "os.PathLike[str]":
    """Per-user JSONL registry of known Cursor subagent conversation ids."""
    from pathlib import Path

    override = os.environ.get("SPONSIO_CURSOR_SUBAGENT_REGISTRY")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".sponsio" / "cursor-subagents.jsonl"


def _is_known_subagent(conversation_id: str | None) -> bool:
    """``True`` iff ``conversation_id`` is recorded as a Cursor subagent."""
    if not conversation_id:
        return False
    from pathlib import Path

    p = Path(_subagent_registry_path())
    if not p.exists():
        return False
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("conversation_id") == conversation_id:
            return True
    return False


def _record_subagent(payload: dict[str, Any]) -> None:
    """Persist a subagentStart event so later preToolUse calls in this
    subagent route to ``_host_subagent``.  No-op on write failure — a
    Sponsio bug must never wedge Cursor's hook chain."""
    from pathlib import Path

    # Cursor's subagentStart payload uses ``subagent_id`` as the
    # subagent's conversation id.  Save just the fields we'll need to
    # look up + audit; skip everything else to keep the file small.
    record = {
        "conversation_id": payload.get("subagent_id") or payload.get("conversation_id"),
        "subagent_type": payload.get("subagent_type"),
        "parent_conversation_id": payload.get("parent_conversation_id"),
    }
    if not record["conversation_id"]:
        return
    p = Path(_subagent_registry_path())
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    except OSError:
        return


def cursor_event_to_sponsio_event(
    cursor_payload: dict[str, Any], hook_event: str
) -> dict[str, Any]:
    """Translate one Cursor hook payload into the dict shape
    :func:`sponsio.guard_stdin.evaluate_event` expects.
    """
    # Convert Cursor's hook event name into Sponsio's hook_event_name
    # vocabulary (PreToolUse / PostToolUse — Claude-Code-shaped).
    if hook_event in (
        "preToolUse",
        "beforeShellExecution",
        "beforeMCPExecution",
        "beforeReadFile",
        "beforeTabFileRead",
    ):
        sponsio_hook = "PreToolUse"
    else:
        sponsio_hook = "PostToolUse"

    if hook_event == "beforeShellExecution":
        tool_name = "Bash"
        tool_input = {
            "command": cursor_payload.get("command", ""),
            "cwd": cursor_payload.get("cwd"),
            "sandbox": cursor_payload.get("sandbox"),
        }
    elif hook_event == "beforeReadFile":
        tool_name = "Read"
        tool_input = {
            "file_path": cursor_payload.get("file_path", ""),
            # Pass content through too — credential-shape sto atoms can
            # match against it if the user's pack opts in.
            "content": cursor_payload.get("content", ""),
        }
    elif hook_event in ("beforeTabFileRead",):
        tool_name = "Read"
        tool_input = {
            "file_path": cursor_payload.get("file_path", ""),
            "content": cursor_payload.get("content", ""),
        }
    elif hook_event == "beforeMCPExecution":
        tool_name = _normalise_tool_name(
            "MCP:" + str(cursor_payload.get("tool_name", ""))
        )
        tool_input = _coerce_tool_input(cursor_payload.get("tool_input"))
    elif hook_event == "afterShellExecution":
        tool_name = "Bash"
        tool_input = {
            "command": cursor_payload.get("command", ""),
            "output": cursor_payload.get("output", ""),
        }
    elif hook_event == "afterMCPExecution":
        tool_name = _normalise_tool_name(
            "MCP:" + str(cursor_payload.get("tool_name", ""))
        )
        tool_input = _coerce_tool_input(cursor_payload.get("tool_input"))
    else:
        # preToolUse, postToolUse, postToolUseFailure, …
        tool_name = _normalise_tool_name(str(cursor_payload.get("tool_name", "")))
        tool_input = _coerce_tool_input(cursor_payload.get("tool_input"))

    sponsio_event: dict[str, Any] = {
        "tool_name": tool_name,
        "tool_input": tool_input,
        "hook_event_name": sponsio_hook,
        # Tag the host so guard_stdin's plugin-id derivation can route
        # to a Cursor-flavoured fallback library if/when one is added.
        # For now ``_host`` covers it because tool names are aligned.
        "host": "cursor",
    }

    # Carry over correlation fields if Cursor provided them — these
    # surface in trace logs and make `sponsio report` searchable.
    for key in (
        "conversation_id",
        "tool_use_id",
        "generation_id",
        "cursor_version",
        "cwd",
    ):
        if key in cursor_payload:
            sponsio_event[key] = cursor_payload[key]

    # Subagent routing: if this conversation_id was registered by a prior
    # ``subagentStart`` event, set ``agent_id`` so guard_stdin's
    # ``derive_plugin_id`` routes to ``_host_subagent`` (the same path
    # Claude Code's Task hook payload exercises).  guard_stdin treats
    # the field's *presence* (non-empty) as the subagent signal.
    conv_id = sponsio_event.get("conversation_id")
    if isinstance(conv_id, str) and _is_known_subagent(conv_id):
        sponsio_event["agent_id"] = conv_id

    return sponsio_event


# ---------------------------------------------------------------------------
# Reply rendering: Sponsio outcome → Cursor JSON + exit code
# ---------------------------------------------------------------------------


def _shell_quote_for_printf(text: str) -> str:
    """Escape ``text`` so it's safe to embed in a single-quoted shell
    argument used by the rewrite-deny workaround.  No newlines in the
    output (collapsed to spaces); single quotes neutralised."""
    flat = text.replace("\n", " ").replace("\r", " ")
    # Single-quoted shell strings can't contain a single quote — close,
    # escape, reopen.
    return flat.replace("'", "'\\''")


def render_cursor_reply(
    outcome: GuardOutcome,
    hook_event: str,
    *,
    rewrite_deny: bool = False,
    cursor_payload: dict[str, Any] | None = None,
) -> tuple[str, int]:
    """Convert a guard outcome into ``(stdout_payload, exit_code)`` for
    Cursor.  Cursor honours both the JSON decision and exit code 2 as
    deny signals; we send both for redundancy.

    ``rewrite_deny`` (opt-in) trades semantic purity for actual signal
    delivery to the model.  Cursor 2.0.64 → 2.2.43+ has a confirmed
    regression where ``agent_message`` is silently dropped on deny —
    the model never learns *why* its tool call failed and tends to
    retry blindly.  When ``rewrite_deny`` is on, instead of returning
    ``permission: deny`` for shell-shaped events, we return
    ``permission: allow`` with ``updated_input.command`` rewritten to a
    ``printf ... ; exit 1`` that prints the deny reason on stderr.
    The agent reads the printf output as a normal tool failure and
    has the deny reason in conversation context — a strict UX
    improvement until Cursor fixes the regression.

    Tracking: https://forum.cursor.com/t/regression-hook-response-fields-user-message-agent-message-still-ignored-in-windows-v2-0-77/142589
    """
    if outcome.allowed:
        # Empty stdout + 0 is the cheapest "allow".
        return "", 0

    # For post-* events Cursor doesn't accept a deny; just surface the
    # reason via additional_context for trace visibility.
    if hook_event.startswith("post") or hook_event.startswith("after"):
        payload = {
            "additional_context": (
                f"[sponsio] post-hoc violation observed: {outcome.reason}"
            )
        }
        return json.dumps(payload), 0

    # beforeSubmitPrompt uses {"continue": false} instead of permission.
    if hook_event == "beforeSubmitPrompt":
        payload: dict[str, Any] = {
            "continue": False,
            "user_message": f"Sponsio blocked submission: {outcome.reason}",
        }
        return json.dumps(payload), 2

    # Rewrite-deny workaround for Cursor's agent_message regression.
    # Only meaningful for shell-shaped events where ``updated_input``
    # carries a ``command`` field; for Read / MCP we fall through to
    # the standard deny.
    if rewrite_deny and hook_event in ("preToolUse", "beforeShellExecution"):
        original_cmd = ""
        if cursor_payload:
            if hook_event == "beforeShellExecution":
                original_cmd = str(cursor_payload.get("command") or "")
            else:
                ti = cursor_payload.get("tool_input") or {}
                if isinstance(ti, dict):
                    original_cmd = str(ti.get("command") or "")
        # Only apply the trick to shell-tool calls — Read/MCP don't
        # carry a ``command`` we can rewrite.
        if original_cmd:
            quoted_reason = _shell_quote_for_printf(outcome.reason)
            quoted_orig = _shell_quote_for_printf(original_cmd)
            replacement = (
                f"printf '%s\\n' "
                f"'[sponsio] BLOCKED by contract ({outcome.plugin_id}): {quoted_reason}' "
                f"'[sponsio] original command suppressed: {quoted_orig}' "
                f"'[sponsio] adjust your approach or ask the user to override.' "
                f">&2; exit 1"
            )
            updated: dict[str, Any] = {}
            if hook_event == "beforeShellExecution":
                updated["command"] = replacement
            else:
                updated["tool_input"] = {"command": replacement}
            payload = {
                "permission": "allow",
                "updated_input": updated,
                "user_message": f"Sponsio blocked: {outcome.reason}",
            }
            return json.dumps(payload), 0

    payload = {
        "permission": "deny",
        "user_message": f"Sponsio blocked this tool call: {outcome.reason}",
        "agent_message": (
            f"[sponsio] denied by contract ({outcome.plugin_id}): {outcome.reason}. "
            "Address the policy violation or ask the user to override."
        ),
    }
    return json.dumps(payload), 2


# ---------------------------------------------------------------------------
# Top-level entry point used by ``sponsio cursor guard``
# ---------------------------------------------------------------------------


def run_cursor_stdin(hook_event: str, stdin_text: str | None = None) -> int:
    """Read one Cursor hook payload from stdin, evaluate it, write the
    Cursor-shaped reply.  Always exits cleanly: a Sponsio bug must
    never wedge a Cursor tool call.

    Set the env var ``SPONSIO_CURSOR_REWRITE_DENY=1`` to enable the
    rewrite-deny workaround (see :func:`render_cursor_reply`); this is
    the recommended setting while the Cursor ``agent_message``
    regression is unfixed.
    """
    raw = stdin_text if stdin_text is not None else sys.stdin.read()
    if not raw.strip():
        return 0
    try:
        cursor_payload = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"sponsio cursor guard:invalid JSON on stdin: {e}\n")
        return 0
    if not isinstance(cursor_payload, dict):
        sys.stderr.write("sponsio cursor guard:stdin payload must be a JSON object\n")
        return 0

    # ``subagentStart`` doesn't gate any tool call — it's the signal that
    # later preToolUse events from this conversation_id should route to
    # ``_host_subagent``.  Record + return ALLOW immediately.
    if hook_event == "subagentStart":
        _record_subagent(cursor_payload)
        return 0

    sponsio_event = cursor_event_to_sponsio_event(cursor_payload, hook_event)

    rewrite_deny = os.environ.get("SPONSIO_CURSOR_REWRITE_DENY", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    try:
        outcome = evaluate_event(sponsio_event)
    except Exception as e:  # pragma: no cover — surfaced via stderr
        sys.stderr.write(f"sponsio cursor guard:evaluation error: {e}\n")
        return 0

    payload, code = render_cursor_reply(
        outcome,
        hook_event,
        rewrite_deny=rewrite_deny,
        cursor_payload=cursor_payload,
    )
    if payload:
        sys.stdout.write(payload)
        sys.stdout.write("\n")
    return code
