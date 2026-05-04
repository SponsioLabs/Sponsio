"""Integration tests for ``sponsio plugin guard --stdin`` (Mode A — host-installed plugin).

These exercise the in-process entry point in :mod:`sponsio.guard_stdin`
end to end: build a temporary per-plugin library tree, point
``$SPONSIO_PLUGIN_ROOT`` at it, push a synthetic Claude Code hook event
through ``run_stdin``, and assert on the rendered deny / allow reply
plus the exit code.

The tests do NOT spawn a real subprocess — that's covered by the
benchmark script. Here we validate routing + decision logic so a
parser or runtime regression breaks pytest, not Claude Code itself.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sponsio.guard_stdin import (
    GuardOutcome,
    derive_plugin_id,
    evaluate_event,
    render_reply,
    run_stdin,
)


# ---------------------------------------------------------------------------
# Plugin-id routing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool_name", "expected"),
    [
        ("Bash", "_host"),
        ("Edit", "_host"),
        ("Write", "_host"),
        ("Read", "_host"),
        ("WebFetch", "_host"),
        ("acme:fetch_data", "acme"),
        ("my-plugin:hello", "my-plugin"),
        ("mcp__acme__fetch", "acme"),
        ("mcp__github__create_issue", "github"),
        ("", "_host"),
        ("UnknownTool", "_host"),
    ],
)
def test_derive_plugin_id(tool_name, expected):
    assert derive_plugin_id(tool_name) == expected


@pytest.mark.parametrize(
    ("tool_name", "host", "expected"),
    [
        # Unknown tool with host=openclaw → _host_openclaw fallback
        ("exec", "openclaw", "_host_openclaw"),
        ("read_file", "openclaw", "_host_openclaw"),
        ("", "openclaw", "_host_openclaw"),
        ("UnknownTool", "openclaw", "_host_openclaw"),
        # Explicit host=claude-code now routes to its dedicated bucket
        # so per-IDE rules can diverge (cf. Cursor's _host_cursor).
        # Callers that don't tag a host (legacy entry point) keep
        # routing to the original ``_host`` for backward compatibility.
        ("UnknownTool", "claude-code", "_host_claude_code"),
        # MCP-namespaced tool routes to <server> regardless of host
        ("mcp__github__delete_repo", "openclaw", "github"),
        ("mcp__github__delete_repo", "claude-code", "github"),
        # Host-driven bucketing: a tool name shaped like a Claude Code
        # first-party (``Bash``) but emitted from OpenClaw still routes
        # to the OpenClaw bucket. The host's contract surface is
        # authoritative — letting a stray Bash event hit Claude Code
        # rules from an OpenClaw runtime mixes shapes (``file_path`` vs
        # ``path``) and gives the wrong deny reasons.
        ("Bash", "openclaw", "_host_openclaw"),
    ],
)
def test_derive_plugin_id_host_aware(tool_name, host, expected):
    """``host`` field in the hook payload steers the fallback library
    to the host's own bucket so per-IDE rules and shape conventions
    don't bleed across runtimes.
    """
    assert derive_plugin_id(tool_name, host=host) == expected


# ---------------------------------------------------------------------------
# Library helpers
# ---------------------------------------------------------------------------


def _write_library(root: Path, plugin_id: str, body: str) -> Path:
    plugin_dir = root / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    lib = plugin_dir / "sponsio.yaml"
    lib.write_text(body)
    return lib


def _shell_library() -> str:
    """A minimal _host library that blocks ``rm -rf /``-style commands."""
    return """
version: "1"
agents:
  _host:
    contracts:
      - desc: "Ban recursive deletes of sensitive roots"
        G:
          pattern: arg_blacklist
          args:
            - Bash
            - command
            - - "rm\\\\s+-[rRf]+\\\\s+(/|\\\\$HOME|~)(\\\\s|;|&|\\\\||$)"
"""


# ---------------------------------------------------------------------------
# evaluate_event — the routing + decision core
# ---------------------------------------------------------------------------


def test_evaluate_no_library_allows(tmp_path, monkeypatch):
    """No library file for a plugin → silent allow."""
    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))
    outcome = evaluate_event(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "acme:fetch",
            "tool_input": {"url": "https://x"},
        }
    )
    assert outcome.allowed is True
    assert outcome.plugin_id == "acme"
    assert outcome.library_path is None


def test_evaluate_allows_when_no_rule_matches(tmp_path, monkeypatch):
    """Library exists, command doesn't match any blacklist → allow."""
    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))
    _write_library(tmp_path, "_host", _shell_library())
    outcome = evaluate_event(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la"},
        }
    )
    assert outcome.allowed is True
    assert outcome.plugin_id == "_host"
    assert outcome.library_path and outcome.library_path.endswith("_host/sponsio.yaml")


def test_evaluate_blocks_dangerous_rm(tmp_path, monkeypatch):
    """``rm -rf /`` matches the shipped pack's blacklist → deny."""
    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))
    _write_library(tmp_path, "_host", _shell_library())
    outcome = evaluate_event(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /"},
        }
    )
    assert outcome.allowed is False
    assert outcome.plugin_id == "_host"
    assert "Bash" in outcome.reason


def test_evaluate_routes_to_per_plugin_library(tmp_path, monkeypatch):
    """Different plugins resolve to different library files.

    Uses the MCP tool-naming convention (``mcp__<server>__<tool>``)
    here; the colon-namespaced form (``plugin:skill``) is covered by
    :func:`test_evaluate_blocks_colon_namespaced_tool` below.
    """
    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))
    # Plugin "acme" gets a rule that bans http: scheme on its fetch tool.
    _write_library(
        tmp_path,
        "acme",
        """
version: "1"
agents:
  acme:
    contracts:
      - desc: "mcp__acme__fetch must use https"
        G:
          pattern: arg_blacklist
          args: [mcp__acme__fetch, url, ["^http://"]]
""",
    )
    # _host has the shell rule from the other library — should NOT
    # apply when the acme tool is called.
    _write_library(tmp_path, "_host", _shell_library())

    # Allowed: https URL on acme's tool.
    ok = evaluate_event(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "mcp__acme__fetch",
            "tool_input": {"url": "https://example.com"},
        }
    )
    assert ok.allowed is True
    assert ok.plugin_id == "acme"

    # Blocked: http URL on acme's tool.
    blocked = evaluate_event(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "mcp__acme__fetch",
            "tool_input": {"url": "http://example.com"},
        }
    )
    assert blocked.allowed is False
    assert blocked.plugin_id == "acme"


def test_evaluate_blocks_colon_namespaced_tool(tmp_path, monkeypatch):
    """Claude Code's namespaced-skill form (``plugin:skill``) routes to
    the plugin library and grounds correctly against ``arg_blacklist``.

    This is the case that motivated the
    ``_is_namespaced_tool_name`` heuristic: previously the colon was
    always parsed as a ``tool:argpattern`` shortcut, so the trace
    event's literal tool name (``acme:fetch``) never matched the
    formula's ``called_with('acme', 'fetch')`` shape — and
    ``arg_field_has`` was never bound either.
    """
    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))
    _write_library(
        tmp_path,
        "acme",
        """
version: "1"
agents:
  acme:
    contracts:
      - desc: "acme:fetch must use https"
        G:
          pattern: arg_blacklist
          args: ["acme:fetch", url, ["^http://"]]
""",
    )

    blocked = evaluate_event(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "acme:fetch",
            "tool_input": {"url": "http://example.com"},
        }
    )
    assert blocked.allowed is False
    assert blocked.plugin_id == "acme"

    allowed = evaluate_event(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "acme:fetch",
            "tool_input": {"url": "https://example.com"},
        }
    )
    assert allowed.allowed is True


# ---------------------------------------------------------------------------
# render_reply — the wire format Claude Code expects
# ---------------------------------------------------------------------------


def test_render_allow_is_silent():
    payload, code = render_reply(
        {"hook_event_name": "PreToolUse"},
        GuardOutcome(allowed=True, plugin_id="_host"),
    )
    assert payload == ""
    assert code == 0


def test_render_pretooluse_deny_uses_permission_decision():
    payload, code = render_reply(
        {"hook_event_name": "PreToolUse"},
        GuardOutcome(allowed=False, reason="rm -rf banned", plugin_id="_host"),
    )
    assert code == 0
    obj = json.loads(payload)
    spec = obj["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse"
    assert spec["permissionDecision"] == "deny"
    assert spec["permissionDecisionReason"] == "rm -rf banned"


def test_render_posttooluse_deny_uses_top_level_decision():
    payload, code = render_reply(
        {"hook_event_name": "PostToolUse"},
        GuardOutcome(allowed=False, reason="audit failed", plugin_id="_host"),
    )
    assert code == 0
    obj = json.loads(payload)
    assert obj["decision"] == "block"
    assert obj["reason"] == "audit failed"


# ---------------------------------------------------------------------------
# run_stdin — full I/O entry point
# ---------------------------------------------------------------------------


def test_run_stdin_empty_input_allows():
    assert run_stdin("") == 0
    assert run_stdin("   \n") == 0


def test_run_stdin_invalid_json_does_not_block(capsys):
    assert run_stdin("not json") == 0
    captured = capsys.readouterr()
    assert "invalid JSON" in captured.err
    assert captured.out == ""


def test_run_stdin_block_emits_deny_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))
    _write_library(tmp_path, "_host", _shell_library())
    payload = json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /"},
        }
    )
    code = run_stdin(payload)
    assert code == 0
    captured = capsys.readouterr()
    obj = json.loads(captured.out.strip())
    assert obj["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_run_stdin_allow_is_silent(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))
    _write_library(tmp_path, "_host", _shell_library())
    payload = json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la"},
        }
    )
    code = run_stdin(payload)
    assert code == 0
    captured = capsys.readouterr()
    assert captured.out == ""


def test_run_stdin_observe_mode_does_not_block(tmp_path, monkeypatch, capsys):
    """``SPONSIO_GUARD_MODE=observe`` lets dangerous calls through.

    Useful for pilot rollouts where operators want logs first.
    """
    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))
    monkeypatch.setenv("SPONSIO_GUARD_MODE", "observe")
    _write_library(tmp_path, "_host", _shell_library())
    payload = json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /"},
        }
    )
    code = run_stdin(payload)
    assert code == 0
    captured = capsys.readouterr()
    # Observe mode → no deny JSON, but the violation is still logged
    # to the session-log dir (not asserted here).
    assert captured.out == ""


# ---------------------------------------------------------------------------
# Hook-payload ``context`` field — bridges host-side caller identity /
# tenant / signed-message metadata into the contract layer so ``ctx``-
# based contracts (``ctx_required`` / ``ctx_matches_required``) fire.
# ---------------------------------------------------------------------------


def _ctx_required_library(tool: str, key: str, allowed: list[str]) -> str:
    """Library that allows ``tool`` only when ``ctx[key]`` is one of
    ``allowed`` — proves the hook payload's ``context`` field reaches
    the contract layer."""
    values = ", ".join(repr(v) for v in allowed)
    return f"""
version: "1"
agents:
  _host:
    contracts:
      - desc: "{tool} only when ctx[{key}] in [{values}]"
        G:
          pattern: ctx_required
          args: [{tool}, {key}, [{values}]]
"""


def test_evaluate_context_field_allows_matching_caller(tmp_path, monkeypatch):
    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))
    _write_library(
        tmp_path, "_host", _ctx_required_library("Bash", "user_id", ["alice"])
    )
    outcome = evaluate_event(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "context": {"user_id": "alice"},
        }
    )
    assert outcome.allowed is True


def test_evaluate_context_field_denies_non_matching_caller(tmp_path, monkeypatch):
    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))
    _write_library(
        tmp_path, "_host", _ctx_required_library("Bash", "user_id", ["alice"])
    )
    outcome = evaluate_event(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "context": {"user_id": "bob"},
        }
    )
    assert outcome.allowed is False


def test_evaluate_missing_context_denies_when_ctx_required(tmp_path, monkeypatch):
    """No ``context`` field at all → ctx[key] is unset → ``ctx_required``
    can't be satisfied → deny. This is the safe-by-default behaviour: a
    host that forgets to forward identity gets blocked, not silently
    allowed."""
    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))
    _write_library(
        tmp_path, "_host", _ctx_required_library("Bash", "user_id", ["alice"])
    )
    outcome = evaluate_event(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        }
    )
    assert outcome.allowed is False


def test_evaluate_context_field_back_compat_for_unrelated_rules(tmp_path, monkeypatch):
    """Hook payloads that do NOT carry a ``context`` field must still
    work for non-ctx rules — this is the back-compat lock so existing
    Cursor / Claude Code / OpenClaw integrations keep working without
    a hook protocol bump."""
    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))
    _write_library(tmp_path, "_host", _shell_library())
    outcome = evaluate_event(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la"},
            # no "context" field
        }
    )
    assert outcome.allowed is True


def test_evaluate_context_field_ignores_non_dict_value(tmp_path, monkeypatch):
    """Defensive: a host that ships a malformed ``context`` field
    (string, list, …) must not blow up the guard — the field is
    silently ignored and evaluation falls back to the no-context
    code path."""
    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))
    _write_library(tmp_path, "_host", _shell_library())
    outcome = evaluate_event(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la"},
            "context": "alice",  # malformed — should be a dict
        }
    )
    assert outcome.allowed is True
