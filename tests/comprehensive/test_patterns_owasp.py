"""Comprehensive coverage — OWASP / Agentic Security patterns (Python).

Covers ``destructive_action_gate``, ``untrusted_source_gate``,
``required_steps_completion``, ``tool_allowlist``,
``dangerous_bash_commands``, ``dangerous_sql_verbs``,
``irreversible_once``, ``confirm_after_source``. Mirrors
``ts/packages/sdk/src/__tests__/comprehensive_owasp.test.ts``.
"""

from __future__ import annotations

from sponsio.patterns.library import (
    confirm_after_source,
    dangerous_bash_commands,
    dangerous_sql_verbs,
    destructive_action_gate,
    irreversible_once,
    required_steps_completion,
    tool_allowlist,
    untrusted_source_gate,
)

from ._helpers import make_guard as _guard


# ── destructive_action_gate ──────────────────────────────────────────


def test_destructive_action_gate_blocks_without_confirm():
    g = _guard(destructive_action_gate("drop_table"))
    assert g.guard_before("drop_table", {"table": "users"}).blocked


# ── untrusted_source_gate (A/G pair) ─────────────────────────────────


def test_untrusted_source_gate_blocks_sink_without_confirm():
    # Python returns (assumption, guarantee) as a 2-tuple of DetFormula.
    assumption, guarantee = untrusted_source_gate(["web_fetch"], ["send_email"])
    g = _guard({"assumption": assumption, "guarantee": guarantee})
    g.guard_before("web_fetch", {"url": "https://attacker.com"})
    assert g.guard_before("send_email", {}).blocked


def test_untrusted_source_gate_allows_when_confirm_present():
    assumption, guarantee = untrusted_source_gate(["web_fetch"], ["send_email"])
    g = _guard({"assumption": assumption, "guarantee": guarantee})
    g.guard_before("web_fetch", {})
    # Python's untrusted_source_gate hard-codes the confirm step name to
    # ``confirm_reconfirmed`` (see desc above) — pass that.
    g.guard_before("confirm_reconfirmed", {})
    assert not g.guard_before("send_email", {}).blocked


# ── required_steps_completion ────────────────────────────────────────


def test_required_steps_completion_blocks_when_steps_missing():
    g = _guard(
        required_steps_completion("close_incident", ["root_cause", "postmortem"]),
    )
    g.guard_before("close_incident", {})
    g.guard_before("root_cause", {})
    # Liveness — second step never ran. Surface as a pending verdict
    # at session-end.
    pending = g.finish_session()
    assert any("close_incident" in str(v) for v in pending)


def test_required_steps_completion_satisfied_when_all_steps_run():
    g = _guard(
        required_steps_completion("close_incident", ["root_cause", "postmortem"]),
    )
    g.guard_before("close_incident", {})
    g.guard_before("root_cause", {})
    g.guard_before("postmortem", {})
    pending = g.finish_session()
    assert pending == []


# ── tool_allowlist ───────────────────────────────────────────────────


def test_tool_allowlist_blocks_disallowed_tool():
    g = _guard(tool_allowlist(["read_file", "list_files"]))
    assert g.guard_before("rm_rf", {}).blocked


def test_tool_allowlist_allows_listed_tool():
    g = _guard(tool_allowlist(["read_file", "list_files"]))
    assert not g.guard_before("read_file", {"path": "/tmp/x"}).blocked


# ── dangerous_bash_commands ──────────────────────────────────────────


def test_dangerous_bash_commands_blocks_default_set():
    g = _guard(dangerous_bash_commands())
    assert g.guard_before("bash", {"command": "rm -rf /"}).blocked


def test_dangerous_bash_commands_allows_safe():
    g = _guard(dangerous_bash_commands())
    assert not g.guard_before("bash", {"command": "ls /tmp"}).blocked


def test_dangerous_bash_commands_blocks_long_and_reordered_rm_flags():
    # The default used to be the literal "rm -rf"; every other spelling
    # of a recursive delete walked through.
    for cmd in [
        "rm --recursive --force /",
        "rm -fr /tmp/x",
        "rm -Rf /tmp/x",
        "rm -f -r /tmp/x",
        "rm -v --recursive /tmp/x",
    ]:
        g = _guard(dangerous_bash_commands())
        assert g.guard_before("bash", {"command": cmd}).blocked, cmd


def test_dangerous_bash_commands_allows_non_recursive_rm():
    for cmd in ["rm -f build.log", "rm notes.txt", "rmdir -p empty/dir"]:
        g = _guard(dangerous_bash_commands())
        assert not g.guard_before("bash", {"command": cmd}).blocked, cmd


# ── dangerous_sql_verbs ──────────────────────────────────────────────


def test_dangerous_sql_verbs_blocks_drop():
    g = _guard(dangerous_sql_verbs())
    assert g.guard_before("execute_sql", {"query": "DROP TABLE users"}).blocked


def test_dangerous_sql_verbs_allows_select():
    g = _guard(dangerous_sql_verbs())
    assert not g.guard_before("execute_sql", {"query": "SELECT * FROM users"}).blocked


# ── irreversible_once ────────────────────────────────────────────────


def test_irreversible_once_allows_first_blocks_second():
    g = _guard(irreversible_once("launch_rocket"))
    assert not g.guard_before("launch_rocket", {}).blocked
    assert g.guard_before("launch_rocket", {}).blocked


# ── confirm_after_source (A/G pair) ──────────────────────────────────


def test_confirm_after_source_blocks_action_without_confirm():
    assumption, guarantee = confirm_after_source("web_fetch", "send_email")
    g = _guard({"assumption": assumption, "guarantee": guarantee})
    g.guard_before("web_fetch", {})
    assert g.guard_before("send_email", {}).blocked


def test_confirm_after_source_allows_when_confirmed():
    assumption, guarantee = confirm_after_source("web_fetch", "send_email")
    g = _guard({"assumption": assumption, "guarantee": guarantee})
    g.guard_before("web_fetch", {})
    g.guard_before("confirm_send_email", {})
    assert not g.guard_before("send_email", {}).blocked
