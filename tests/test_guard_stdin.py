"""Integration tests for ``sponsio shield guard --stdin`` (Mode A — host-installed shield).

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
        E:
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
    because Claude Code's namespaced-skill form (``acme:fetch``) hits
    a separate grounding limitation around colons in tool names —
    tracked, not in scope for this prototype.
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
        E:
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
