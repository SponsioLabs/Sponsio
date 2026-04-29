"""Regression tests for the Cursor hook protocol.

Pins the exact bug fixed in this commit: ``render_cursor_reply``
returning ``("", 0)`` for allow caused every Cursor tool call to be
blocked under Cursor's default ``failClosed: true``, because Cursor
1.7+ treats empty stdout as "no decision" → fall through to fail-
closed → block.

The fix: always emit an explicit JSON allow response.  Different
events use different shapes:

* ``preToolUse``, ``beforeShellExecution``, ``beforeMCPExecution``,
  ``beforeReadFile`` → ``{"permission": "allow"}``
* ``beforeSubmitPrompt`` → ``{"continue": true}``
* ``postToolUse``, ``afterShellExecution`` → ``{}``

These tests pin every shape so the bug can't reappear silently.
"""

from __future__ import annotations

import json

from sponsio.guard_stdin import GuardOutcome
from sponsio.integrations.cursor import render_cursor_reply


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _allow() -> GuardOutcome:
    return GuardOutcome(allowed=True, plugin_id="_host")


def _deny(reason: str = "test deny reason") -> GuardOutcome:
    return GuardOutcome(allowed=False, reason=reason, plugin_id="_host")


# ---------------------------------------------------------------------------
# §1 — Allow path: explicit JSON, NEVER empty
# ---------------------------------------------------------------------------


def test_allow_pre_tool_use_emits_explicit_permission_json():
    """The bug: ``preToolUse`` allow used to emit empty stdout.  Cursor
    with ``failClosed: true`` (the install default) treats empty as
    block → every tool call denied.  Fix: explicit ``{"permission": "allow"}``.
    """
    payload, code = render_cursor_reply(_allow(), "preToolUse")
    assert code == 0
    assert payload, "preToolUse allow MUST NOT emit empty stdout"
    assert json.loads(payload) == {"permission": "allow"}


def test_allow_before_shell_execution_emits_explicit_allow():
    payload, code = render_cursor_reply(_allow(), "beforeShellExecution")
    assert code == 0
    assert json.loads(payload) == {"permission": "allow"}


def test_allow_before_read_file_emits_explicit_allow():
    payload, code = render_cursor_reply(_allow(), "beforeReadFile")
    assert code == 0
    assert json.loads(payload) == {"permission": "allow"}


def test_allow_before_mcp_execution_emits_explicit_allow():
    payload, code = render_cursor_reply(_allow(), "beforeMCPExecution")
    assert code == 0
    assert json.loads(payload) == {"permission": "allow"}


def test_allow_before_submit_prompt_emits_continue_true():
    """beforeSubmitPrompt is a flow gate, not a tool-permission gate.
    Cursor expects ``{"continue": true}`` for allow."""
    payload, code = render_cursor_reply(_allow(), "beforeSubmitPrompt")
    assert code == 0
    assert json.loads(payload) == {"continue": True}


def test_allow_post_tool_use_emits_empty_object():
    """post* / after* events have no permission decision; emit ``{}``."""
    payload, code = render_cursor_reply(_allow(), "postToolUse")
    assert code == 0
    assert json.loads(payload) == {}


def test_allow_after_shell_execution_emits_empty_object():
    payload, code = render_cursor_reply(_allow(), "afterShellExecution")
    assert code == 0
    assert json.loads(payload) == {}


# ---------------------------------------------------------------------------
# §2 — Deny path: explicit JSON + exit 2
# ---------------------------------------------------------------------------


def test_deny_pre_tool_use_emits_permission_deny_with_reason():
    payload, code = render_cursor_reply(_deny("DROP TABLE forbidden"), "preToolUse")
    assert code == 2
    obj = json.loads(payload)
    assert obj["permission"] == "deny"
    assert "DROP TABLE forbidden" in obj["user_message"]
    assert "DROP TABLE forbidden" in obj["agent_message"]


def test_deny_before_shell_execution_emits_permission_deny():
    payload, code = render_cursor_reply(
        _deny("SQL injection forbidden"), "beforeShellExecution"
    )
    assert code == 2
    obj = json.loads(payload)
    assert obj["permission"] == "deny"


def test_deny_before_submit_prompt_emits_continue_false():
    payload, code = render_cursor_reply(
        _deny("blocked input"), "beforeSubmitPrompt"
    )
    assert code == 2
    obj = json.loads(payload)
    assert obj["continue"] is False
    assert "blocked input" in obj["user_message"]


def test_deny_post_tool_use_surfaces_via_additional_context():
    """Post-* events can't deny (the call already happened).  The
    contract violation surfaces via ``additional_context`` for trace
    visibility, with exit 0 (no actual block)."""
    payload, code = render_cursor_reply(
        _deny("post-hoc violation"), "postToolUse"
    )
    assert code == 0
    obj = json.loads(payload)
    assert "additional_context" in obj
    assert "post-hoc violation" in obj["additional_context"]


# ---------------------------------------------------------------------------
# §3 — End-to-end: full pipeline through `run_cursor_stdin`
# ---------------------------------------------------------------------------


def test_run_cursor_stdin_allow_returns_explicit_json(monkeypatch, capsys, tmp_path):
    """End-to-end: a benign Cursor preToolUse payload through
    ``run_cursor_stdin`` writes ``{"permission": "allow"}`` to stdout
    and returns exit 0.  This is what would happen in production."""
    from sponsio.integrations.cursor import run_cursor_stdin

    # Empty plugin tree → no rules apply → universally allow.
    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path / "plugins"))
    monkeypatch.setenv("HOME", str(tmp_path))

    payload = json.dumps(
        {
            "event": "preToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": "/tmp/myapp/src/api.py"},
            "conversation_id": "cursor-test-1",
        }
    )
    code = run_cursor_stdin("preToolUse", payload)
    out = capsys.readouterr().out.strip()
    assert code == 0
    assert out, "allow MUST emit non-empty stdout (regression: this used to be empty)"
    assert json.loads(out) == {"permission": "allow"}
