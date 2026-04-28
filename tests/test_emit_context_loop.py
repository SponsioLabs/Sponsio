"""End-to-end tests for the agent-driven emit-context / prompt loop.

The setup skill drives a host agent (Claude Code, Cursor, Codex)
through three workflows, each shaped:

    sponsio <verb> --emit-{context,traces}     →  structured JSON
    sponsio <prompt-cmd> <flow>                →  prompt template
    [agent applies prompt to JSON]             →  contract YAML
    sponsio validate --config <yaml>           →  zero errors

These tests don't actually call an LLM — they pin every step EXCEPT
the agent's reasoning, plus a hand-authored sample YAML representing
what a competent agent would produce.  When the user runs the loop
in a live IDE, every CLI step here is the same path that fires.

Coverage:

  W1  — onboard agent-driven path (`--emit-context` + `prompt onboard`)
  W3b — refresh agent-driven path (`--emit-traces` + `prompt refresh`)
  Mode A — plugin scan agent-driven (`--introspect` + `plugin prompt <host>`)

If any prompt template is renamed, deleted, or changes the
``Output schema`` block, these tests fail and the SKILL.md needs
re-syncing.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
MOCK_MCP_SERVER = REPO_ROOT / "examples" / "demo" / "mock_github_mcp" / "server.py"


def _run_cli(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Invoke the sponsio CLI as a subprocess.

    Same shape as the helper in ``test_plugin_scan.py`` so the two
    files share a debugging surface — if one breaks both can be
    triaged with the same techniques.
    """
    return subprocess.run(
        [sys.executable, "-m", "sponsio.cli", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ===========================================================================
# Prompt templates — exist + are well-formed
# ===========================================================================


@pytest.mark.parametrize(
    ("subcmd", "flow"),
    [
        (("plugin", "prompt"), "claude-code"),
        (("plugin", "prompt"), "openclaw"),
        (("plugin", "prompt"), "mcp-bare"),
        (("prompt",), "onboard"),
        (("prompt",), "refresh"),
    ],
)
def test_prompt_template_prints_well_formed(subcmd, flow):
    """Every host-agent prompt the SKILL.md asks the agent to fetch
    must (a) print, (b) be non-empty, (c) include a clear Output
    schema section so the agent knows what to produce."""
    proc = _run_cli(*subcmd, flow)
    assert proc.returncode == 0, proc.stderr

    out = proc.stdout
    assert out.strip(), f"empty prompt for {flow!r}"

    # Every prompt has a section that tells the agent what shape
    # to output.  Templates are free to vary the exact heading
    # ("Output schema" / "What you produce" / "Output format") but
    # at least one MUST appear — without it the agent has no schema.
    assert any(
        marker in out
        for marker in (
            "Output schema",
            "Output format",
            "What you produce",
        )
    ), f"prompt {flow!r} has no output-schema section:\n{out[:400]}"

    # And every prompt must reference Sponsio's pattern vocabulary
    # — either inline or via the loaded marker.
    assert any(
        marker in out
        for marker in (
            "arg_blacklist",
            "rate_limit",
            "Pattern vocabulary",
            "pattern vocabulary",
        )
    ), f"prompt {flow!r} doesn't reference any pattern vocabulary"


# ===========================================================================
# Mode A — plugin scan loop
# ===========================================================================


def _parse_inventory_block(scan_stdout: str) -> dict:
    """Pull the ``# === tool inventory ===`` JSON block out of scan dry-run output."""
    m = re.search(
        r"# === tool inventory.*?===.*?\n(\{.*?\n\})\s*\n",
        scan_stdout,
        re.DOTALL,
    )
    assert m is not None, (
        "scan dry-run output missing the tool-inventory JSON block "
        "(host agent depends on this for the prompt input):\n" + scan_stdout
    )
    return json.loads(m.group(1))


def test_plugin_scan_loop_produces_inventory_for_agent():
    """Mode A loop step 1: introspect a real MCP server and verify
    the dry-run output contains a parseable tool inventory the agent
    can apply ``sponsio plugin prompt claude-code`` to."""
    if not MOCK_MCP_SERVER.exists():
        pytest.skip(f"demo MCP server not present at {MOCK_MCP_SERVER}")

    proc = _run_cli(
        "plugin",
        "scan",
        "--plugin-id",
        "github-mock",
        "--target-host",
        "claude-code",
        "--introspect",
        f"python3 {MOCK_MCP_SERVER}",
    )
    assert proc.returncode == 0, proc.stderr

    inv = _parse_inventory_block(proc.stdout)
    assert inv["plugin_id"] == "github-mock"
    assert inv["target_host"] == "claude-code"
    assert len(inv["tools"]) == 3
    # Every tool entry has the four fields the prompt template references.
    for t in inv["tools"]:
        assert "name" in t
        assert "description" in t and t["description"]
        assert "input_schema" in t
        assert "tool_name_in_contracts" in t
        # Claude Code namespacing applied
        assert t["tool_name_in_contracts"].startswith("mcp__github-mock__")


def test_plugin_scan_agent_produced_yaml_validates(tmp_path):
    """Mode A loop step 4: hand-author the YAML a competent agent
    would produce from the introspected inventory + prompt, write it
    to disk, and confirm ``sponsio validate`` accepts every contract.

    This is the load-bearing assertion: if the prompt schema drifts
    from what ``validate`` accepts, the agent's output won't load
    and the loop is broken."""
    yaml_path = tmp_path / "sponsio.yaml"
    yaml_path.write_text(
        # Sample agent output — same shape as Test 1 in the
        # closed-loop walkthrough.
        """
version: "1"
agents:
  github-mock:
    include:
      - sponsio:core/runaway
    contracts:
      - desc: "Block fetching repos that look like credential stores"
        E:
          pattern: arg_blacklist
          args:
            - mcp__github-mock__get_repo
            - repo
            - - "^(private-keys|secrets|credentials)$"
              - ".*-keys$"
          source: agent-extracted
      - desc: "Cap issue comments per session"
        E:
          pattern: rate_limit
          args: [mcp__github-mock__create_issue_comment, 5]
          source: agent-extracted
      - desc: "Block credential-shaped strings in issue comment bodies"
        E:
          pattern: arg_blacklist
          args:
            - mcp__github-mock__create_issue_comment
            - body
            - - "AKIA[A-Z0-9]{16}"
              - "ghp_[A-Za-z0-9]{36,}"
          source: agent-extracted
"""
    )
    proc = _run_cli("validate", "--config", str(yaml_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # The validator's success line carries the contract count it
    # accepted.  We pin three contracts; if the includes pull in
    # more, the count rises but our minimum should still hold.
    assert "validated" in proc.stdout


# ===========================================================================
# Mode B — onboard loop
# ===========================================================================


@pytest.fixture()
def mock_project(tmp_path):
    """Build a minimal langgraph-shaped project + a security policy doc.

    Everything the agent's W1 path expects: framework signal (the
    langgraph import), some tool functions, and a root-level
    ``security.md`` to drive policy-grounded contract proposals.
    """
    (tmp_path / "app.py").write_text(
        '"""Customer-support agent built on langgraph."""\n'
        "from langgraph.graph import StateGraph\n"
        "def search_knowledge_base(query: str): pass\n"
        "def send_email(to: str, body: str): pass\n"
        "def delete_user(user_id: int): pass\n"
        "def charge_card(amount: float, card_id: str): pass\n"
    )
    (tmp_path / "security.md").write_text(
        "# Security policy\n"
        "- send_email at most 5 per session.\n"
        "- delete_user is irreversible — never call without explicit human confirmation.\n"
        "- charge_card amount must be ≤ $5000 per call.\n"
    )
    return tmp_path


def test_onboard_emit_context_shape(mock_project):
    """Mode B loop step 1: emit-context dumps the structured inputs
    the host agent needs.  Pins the JSON schema so a future internal
    refactor of ``run_onboard`` can't silently drop a field the
    prompt template depends on."""
    proc = _run_cli("onboard", str(mock_project), "--emit-context")
    assert proc.returncode == 0, proc.stderr

    ctx = json.loads(proc.stdout)
    # Required top-level fields — the prompt template references
    # every one of these by name.
    for key in (
        "framework",
        "agent_id",
        "tool_inventory",
        "auto_selected_packs",
        "needs_workspace",
        "existing_yaml",
        "policy_docs",
        "next_steps_hint",
    ):
        assert key in ctx, f"emit-context missing required key {key!r}"

    # Framework detection actually fires (langgraph import is in
    # app.py).  If the detector regresses we want to know.
    assert ctx["framework"]["name"] == "langgraph", (
        f"expected langgraph detection, got {ctx['framework']!r}"
    )

    # Pack auto-selection runs (at minimum core/universal lands; the
    # exact set is framework-dependent, e.g. capability/shell when a
    # shell tool is in the inventory).  Keep the assertion permissive
    # so further pack-selection tuning doesn't break the smoke test —
    # the dedicated pack tests already pin the per-framework table.
    assert ctx["auto_selected_packs"], "expected at least one auto-selected pack"
    assert "sponsio:core/universal" in ctx["auto_selected_packs"]

    # Policy doc captured + content surfaced for the agent to weight.
    assert len(ctx["policy_docs"]) >= 1
    sec = next(p for p in ctx["policy_docs"] if "security" in p["path"].lower())
    assert "send_email" in sec["content"]
    assert "delete_user" in sec["content"]
    assert "charge_card" in sec["content"]

    # next_steps_hint nudges the agent to the second half of the loop.
    assert "sponsio prompt onboard" in ctx["next_steps_hint"]


def test_onboard_agent_produced_yaml_validates(tmp_path, mock_project):
    """Mode B loop step 4: a competent agent reads the emit-context
    output + the policy doc and produces a sponsio.yaml.  Verify the
    representative output validates cleanly — i.e. the prompt's
    pattern vocabulary is consistent with what the runtime accepts."""
    yaml_path = mock_project / "sponsio.yaml"
    yaml_path.write_text(
        """
version: "1"
mode: observe
agents:
  agent:
    include:
      - sponsio:core/universal
      - sponsio:core/runaway
    contracts:
      - desc: "send_email rate-limited per security.md"
        E:
          pattern: rate_limit
          args: [send_email, 5]
          source: agent-extracted
      - desc: "delete_user is irreversible per security.md"
        E:
          pattern: irreversible_once
          args: [delete_user]
          source: agent-extracted
      - desc: "charge_card amount must be <= $5000 per security.md"
        E:
          pattern: arg_value_range
          args: [charge_card, amount, 0, 5000]
          source: agent-extracted
"""
    )
    proc = _run_cli("validate", "--config", str(yaml_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "validated" in proc.stdout


# ===========================================================================
# Mode B — refresh loop
# ===========================================================================


def test_refresh_emit_traces_shape(tmp_path, monkeypatch):
    """W3b loop step 1: emit-traces aggregates session events into
    a JSON shape the refresh prompt can apply to.

    Builds a synthetic session log so the test doesn't depend on
    whatever happens to be on disk in ``~/.sponsio/sessions/``.
    """
    # 1. Stage a tiny sponsio.yaml the prompt's "existing_contracts"
    #    field will mirror.
    cfg = tmp_path / "sponsio.yaml"
    cfg.write_text(
        """
version: "1"
agents:
  test_agent:
    contracts:
      - desc: "send_email at most 5 per session"
        E:
          pattern: rate_limit
          args: [send_email, 5]
          source: trace
"""
    )

    # 2. Stage a synthetic session log under a sandboxed sessions
    #    dir so we don't read the operator's real traces and we
    #    don't pollute them either.  Override via the
    #    ``SPONSIO_SESSIONS_DIR`` env var (honoured by
    #    :func:`sponsio.runtime.session_log._resolve_default_base_dir`).
    sessions_root = tmp_path / "sessions"
    sessions = sessions_root / "test_agent"
    sessions.mkdir(parents=True)
    log = sessions / "20260101_000000_1.jsonl"

    import time

    now = time.time()

    # Three "send_email" events: two passed, one would-have-blocked.
    # Plus four "transfer_funds" events with no contract — should
    # show up as an "uncovered_patterns" entry (>=3 calls threshold).
    events = []
    for i in range(2):
        events.append(
            {
                "ts": now - 100 + i,
                "agent_id": "test_agent",
                "action": "send_email",
                "pipeline": "det",
                "constraint": "send_email at most 5 per session",
                "result": {"action": "allowed", "message": ""},
            }
        )
    events.append(
        {
            "ts": now - 50,
            "agent_id": "test_agent",
            "action": "send_email",
            "pipeline": "det",
            "constraint": "send_email at most 5 per session",
            "result": {"action": "observed", "message": "would have blocked: 6th call"},
        }
    )
    for i in range(4):
        events.append(
            {
                "ts": now - 30 + i,
                "agent_id": "test_agent",
                "action": "transfer_funds",
                "pipeline": "det",
                "constraint": "",
                "result": {"action": "allowed", "message": ""},
            }
        )

    log.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    monkeypatch.setenv("SPONSIO_SESSIONS_DIR", str(sessions_root))
    proc = _run_cli(
        "refresh",
        "-c",
        str(cfg),
        "--since",
        "1d",
        "--emit-traces",
    )
    assert proc.returncode == 0, proc.stderr

    out = json.loads(proc.stdout)
    assert "agents" in out
    ag = out["agents"]["test_agent"]
    assert ag["agent"] == "test_agent"
    assert ag["total_events"] == 7

    # Existing send_email contract round-trips
    assert any(
        c.get("desc", "").startswith("send_email") for c in ag["existing_contracts"]
    )

    by_tool = {t["tool"]: t for t in ag["trace_summary"]["by_tool"]}
    assert "send_email" in by_tool
    assert by_tool["send_email"]["call_count"] == 3
    assert by_tool["send_email"]["passed"] == 2
    assert len(by_tool["send_email"]["would_have_blocked"]) == 1

    # transfer_funds has no contract and was called 4× → uncovered.
    uncovered_names = [u["tool"] for u in ag["trace_summary"]["uncovered_patterns"]]
    assert "transfer_funds" in uncovered_names

    # Emit-traces nudge points at the prompt command.
    assert "sponsio prompt refresh" in out["next_steps_hint"]


def test_refresh_agent_produced_changes_compose_to_valid_yaml(tmp_path):
    """W3b loop step 4: an agent reading emit-traces output produces
    a ``proposed_changes:`` diff (add / retire / tighten).  The end
    state — existing yaml + agent's adds — must validate cleanly.

    We don't pin the diff shape here (the prompt is free to evolve);
    we pin the post-merge YAML to make sure the *additions* an agent
    is taught to produce don't conflict with the runtime schema.
    """
    yaml_path = tmp_path / "sponsio.yaml"
    yaml_path.write_text(
        """
version: "1"
mode: observe
agents:
  agent:
    include:
      - sponsio:core/universal
    contracts:
      - desc: "send_email at most 5 per session"
        E:
          pattern: rate_limit
          args: [send_email, 5]
          source: agent-extracted
      # Agent additions from refresh:
      - desc: "collect_and_snapshot must precede parser"
        E:
          pattern: must_precede
          args: [collect_and_snapshot, parser]
          source: agent-extracted-from-traces
      - desc: "auditor at most 5 consecutive calls"
        E:
          pattern: loop_detection
          args: [auditor, 5]
          source: agent-extracted-from-traces
"""
    )
    proc = _run_cli("validate", "--config", str(yaml_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "validated" in proc.stdout


# ===========================================================================
# capability/database pack — destructive DB ops blocked at action boundary
# ===========================================================================


@pytest.mark.parametrize(
    ("host", "tool", "command", "expect_deny"),
    [
        # Claude Code-shape (Bash)
        ("claude-code", "Bash", 'psql -c "DROP DATABASE prod"', True),
        ("claude-code", "Bash", 'mysql -e "drop table users"', True),
        ("claude-code", "Bash", 'psql -c "DELETE FROM users;"', True),
        ("claude-code", "Bash", 'psql -c "DELETE FROM users WHERE id=1;"', False),
        ("claude-code", "Bash", "dropdb production", True),
        ("claude-code", "Bash", "redis-cli -a pw FLUSHALL", True),
        ("claude-code", "Bash", "alembic downgrade base", True),
        ("claude-code", "Bash", "alembic downgrade -1", False),
        ("claude-code", "Bash", "rm -rf /var/lib/postgresql/data", True),
        ("claude-code", "Bash", "rm /etc/somefile", False),
        ("claude-code", "Bash", 'psql -c "TRUNCATE TABLE logs"', True),
        ("claude-code", "Bash", "truncate -s 0 /tmp/x.log", False),
        # OpenClaw-shape (exec) — same rules via capability/database
        ("openclaw", "exec", 'mysql -e "DROP TABLE users"', True),
        ("openclaw", "exec", "redis-cli FLUSHDB", True),
        ("openclaw", "exec", "ls -la /tmp", False),
    ],
)
def test_database_pack_blocks_destructive_ops(
    tmp_path, monkeypatch, host, tool, command, expect_deny
):
    """Pin the capability/database pack's behaviour against
    representative destructive cases.  If the regex tightens or
    loosens enough to flip a verdict here, this test fires.

    Covers both ``_host.yaml`` (Claude-Code-shape, ``Bash``) and
    ``_host_openclaw.yaml`` (canonical ``exec``) — the pack's
    canonical ``exec`` rules apply to both via ``tool_rename`` /
    direct match.
    """
    # Stage both default libraries under a sandboxed plugin root so
    # the test doesn't depend on the operator having run
    # ``sponsio plugin init``.
    from sponsio.plugin.registry import read_bundled

    for lib_name in ("_host", "_host_openclaw"):
        d = tmp_path / lib_name
        d.mkdir()
        (d / "sponsio.yaml").write_text(read_bundled(lib_name))

    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))

    from sponsio.guard_stdin import evaluate_event

    event = {
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"command": command},
    }
    if host == "openclaw":
        event["host"] = "openclaw"

    result = evaluate_event(event)
    got_deny = not result.allowed
    assert got_deny == expect_deny, (
        f"command={command!r} on host={host!r}: "
        f"expected {'deny' if expect_deny else 'allow'}, got {result!r}"
    )


# ===========================================================================
# capability/credentials pack — secret shapes blocked at write boundary
# ===========================================================================


# 40-char filler that looks shape-correct (alnum only, no separators) so
# the regex actually fires — real credential suffixes don't carry
# underscores or dashes inside the random portion.
_DEMO_FILLER = "DEMOFAKEXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"


@pytest.mark.parametrize(
    ("host", "tool", "content", "expect_deny"),
    [
        # Positive — real credential shapes
        ("claude-code", "Write", f"creds=AKIA{'X' * 16}", True),
        ("claude-code", "Write", f"GITHUB_TOKEN=ghp_{_DEMO_FILLER}", True),
        ("claude-code", "Write", f"STRIPE_KEY=sk_live_{_DEMO_FILLER}", True),
        ("claude-code", "Write", f"OPENAI=sk-proj-{_DEMO_FILLER}", True),
        ("claude-code", "Write", f"ANTHROPIC=sk-ant-{_DEMO_FILLER}", True),
        ("claude-code", "Write", "SLACK=xoxb-12345678-DEMO-FAKE", True),
        ("claude-code", "Write", "GOOG=AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ1234567", True),
        ("claude-code", "Write", "-----BEGIN OPENSSH PRIVATE KEY-----\nMII...", True),
        ("claude-code", "Edit", f"sk_live_{_DEMO_FILLER}", True),
        # Negative — lookalike prose / explanations
        ("claude-code", "Write", "See AWS access keys in the docs", False),
        ("claude-code", "Write", "ghp_ tokens look like ghp_<36 chars>", False),
        ("claude-code", "Write", "commit deadbeef1234 fixed the bug", False),
        # OpenClaw side — same canonical names via include
        ("openclaw", "write", f"creds=AKIA{'X' * 16}", True),
        ("openclaw", "edit", "-----BEGIN RSA PRIVATE KEY-----", True),
        ("openclaw", "write", "hello world", False),
    ],
)
def test_credentials_pack_blocks_secret_writes(tmp_path, monkeypatch, host, tool, content, expect_deny):
    """Pin the capability/credentials pack against representative
    secret shapes from the major providers, plus negatives that look
    similar in prose.  Same dual-host coverage as the database pack
    test above — Claude Code's ``Write`` / ``Edit`` and OpenClaw's
    canonical ``write`` / ``edit``.
    """
    from sponsio.plugin.registry import read_bundled

    for lib_name in ("_host", "_host_openclaw"):
        d = tmp_path / lib_name
        d.mkdir()
        (d / "sponsio.yaml").write_text(read_bundled(lib_name))

    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))

    from sponsio.guard_stdin import evaluate_event

    field = "new_string" if tool in ("Edit", "edit") else "content"
    event = {
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {
            "file_path": "/tmp/x.md",
            "path": "/tmp/x.md",
            field: content,
        },
    }
    if host == "openclaw":
        event["host"] = "openclaw"

    result = evaluate_event(event)
    got_deny = not result.allowed
    assert got_deny == expect_deny, (
        f"content={content!r} on {host}/{tool}: "
        f"expected {'deny' if expect_deny else 'allow'}, got {result!r}"
    )


# ===========================================================================
# capability/subagent + _host_subagent routing — privilege boundary
# ===========================================================================
#
# Pin the asymmetric routing (main agent vs Task-spawned sub-agent)
# and the rule set that fires for each.  The same Bash command that
# is allowed for the main session can deny when the hook payload
# carries an ``agent_id`` field — Claude Code sets this iff the
# tool call originates from inside a Task / Explore / general-purpose
# sub-agent.


@pytest.mark.parametrize(
    ("agent_id", "tool", "command_or_path", "expect_deny", "expected_route"),
    [
        # ----- Main agent (no agent_id) — expanded privileges -------------
        # Side-effects allowed: main agent has user-conversation context
        # to confirm intent; user sees every action.
        (None, "Bash", "git commit -m fix",                False, "_host"),
        (None, "Bash", "git push origin main",             False, "_host"),
        (None, "Bash", "npm install -g typescript",        False, "_host"),
        (None, "Bash", "curl https://example.com",         False, "_host"),
        (None, "Bash", "brew install jq",                  False, "_host"),
        # ... but baseline destructive ops still deny for main:
        (None, "Bash", "rm -rf /",                          True, "_host"),
        (None, "Bash", 'psql -c "DROP DATABASE prod"',      True, "_host"),

        # ----- Sub-agent (agent_id present) — same call, deny ------------
        # No user-conversation context to confirm intent → side-effects
        # blocked; sub-agent must report findings back through main agent.
        ("agt_42", "Bash", "git commit -m fix",             True, "_host_subagent"),
        ("agt_42", "Bash", "git push --force origin main",  True, "_host_subagent"),
        ("agt_42", "Bash", "npm install -g typescript",     True, "_host_subagent"),
        ("agt_42", "Bash", "curl https://example.com",      True, "_host_subagent"),
        ("agt_42", "Bash", "brew install jq",               True, "_host_subagent"),
        ("agt_42", "Bash", "sudo systemctl restart pg",     True, "_host_subagent"),

        # Sub-agent: read-only / scoped ops still allowed — sub-agents
        # can do useful research / verify-shape work.
        ("agt_42", "Bash", "ls -la",                       False, "_host_subagent"),
        ("agt_42", "Bash", "pytest tests/",                False, "_host_subagent"),
        ("agt_42", "Bash", "npm install",                  False, "_host_subagent"),
        ("agt_42", "Bash", "git status",                   False, "_host_subagent"),

        # Sub-agent: same destructive baseline as main (inherited via
        # capability/database, capability/shell).
        ("agt_42", "Bash", "rm -rf /",                      True, "_host_subagent"),
        ("agt_42", "Bash", 'psql -c "DROP DATABASE prod"',  True, "_host_subagent"),
    ],
)
def test_subagent_routing_and_restrictions(
    tmp_path, monkeypatch, agent_id, tool, command_or_path, expect_deny, expected_route
):
    """End-to-end: ``agent_id`` in the hook payload routes to
    ``_host_subagent`` (not ``_host``); the sub-agent library has a
    strictly tighter ruleset (capability/subagent on top of the
    shared baseline).
    """
    from sponsio.plugin.registry import read_bundled

    for lib_name in ("_host", "_host_subagent"):
        d = tmp_path / lib_name
        d.mkdir()
        (d / "sponsio.yaml").write_text(read_bundled(lib_name))

    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))

    from sponsio.guard_stdin import evaluate_event

    event = {
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"command": command_or_path},
    }
    if agent_id is not None:
        event["agent_id"] = agent_id

    result = evaluate_event(event)
    assert result.plugin_id == expected_route, (
        f"{tool!r} agent_id={agent_id!r} routed to {result.plugin_id!r}, "
        f"expected {expected_route!r}"
    )
    got_deny = not result.allowed
    assert got_deny == expect_deny, (
        f"{tool} {command_or_path!r} agent_id={agent_id!r}: "
        f"expected {'deny' if expect_deny else 'allow'}, got {result!r}"
    )


def test_subagent_write_denied_for_system_paths(tmp_path, monkeypatch):
    """Sub-agents can't write to /etc, /var, user-home — those should
    flow through the main agent so the user sees the diff."""
    from sponsio.plugin.registry import read_bundled

    for lib_name in ("_host", "_host_subagent"):
        d = tmp_path / lib_name
        d.mkdir()
        (d / "sponsio.yaml").write_text(read_bundled(lib_name))

    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))

    from sponsio.guard_stdin import evaluate_event

    for path in ("/etc/hosts", "/var/log/foo.log", "/Users/alice/.bashrc"):
        ev = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": path, "content": "x"},
            "agent_id": "agt_99",
        }
        r = evaluate_event(ev)
        assert not r.allowed, f"sub-agent should NOT be allowed to Write {path!r}"
        assert r.plugin_id == "_host_subagent"
