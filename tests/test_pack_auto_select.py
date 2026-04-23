"""Tests for ``select_packs`` and the wider auto-selection path in
``run_onboard``.

Onboarding's value prop hinges on the user landing in a state where
useful contracts are already in place after one command.  The
contract packs (``sponsio:core/universal`` etc.) are how we get
there — but only if they're picked automatically.  Asking users to
read pack docs and copy include lines defeats the smoothness goal
that motivated this whole feature.

What's pinned here:

* **Universal always selected.**  The universal pack applies to
  every LLM agent, so it should never be omitted.
* **Runaway gated by framework.**  Only multi-step / agentic-loop
  frameworks need budget controls; one-shot completion calls don't.
* **Capability packs gated by tool names.**  Shell pack picked when
  a tool name resembles shell exec (`bash`, `run_command`, …);
  filesystem pack picked when names resemble file IO (`read_file`,
  `write_file`, …).
* **Tool-rename pre-populated.**  When a host's tool name differs
  from the pack's canonical name (`bash` vs `exec`), `tool_rename`
  is filled in so included rules apply out of the box without
  manual edits.
* **`workspace:` propagated.**  Selecting the filesystem pack
  triggers a `workspace:` line so the load-time placeholder check
  passes.
* **End-to-end via `run_onboard` produces a config that loads
  cleanly** — the strongest possible signal that the auto-selection
  picks integrate with the rest of the loader.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from sponsio.config import load_config
from sponsio.onboard import (
    PackSelection,
    _emit_pack_block,
    _splice_pack_block_into_agent,
    run_onboard,
    select_packs,
)


# ---------------------------------------------------------------------------
# 1. select_packs — the heuristics
# ---------------------------------------------------------------------------


def _t(*names: str) -> list[dict]:
    """Shorthand for tool_inventory shape."""
    return [{"name": n} for n in names]


class TestSelectPacks:
    def test_universal_always_first(self):
        sel = select_packs("openai", _t("lookup_order"))
        assert sel.packs[0] == "sponsio:core/universal"

    def test_universal_only_for_one_shot_chat(self):
        """Bare openai chat — no agentic loop, no shell, no fs.  Just
        the universal pack so PII / injection guards still apply."""
        sel = select_packs("openai", _t("lookup_order"))
        assert sel.packs == ["sponsio:core/universal"]
        assert sel.tool_rename == {}
        assert sel.needs_workspace is False

    @pytest.mark.parametrize(
        "fw", ["langgraph", "langchain", "crewai", "openai_agents", "claude_agent"]
    )
    def test_runaway_picked_for_agentic_frameworks(self, fw):
        """Agentic loops have budget exposure (token spend, infinite
        recursion, fan-out).  The runaway pack's loop_detection /
        token_budget rules are exactly what catches that, but only
        useful when the loop exists.  Pin the gate."""
        sel = select_packs(fw, _t("any_tool"))
        assert "sponsio:core/runaway" in sel.packs

    @pytest.mark.parametrize("fw", ["openai", "none", "vercel_ai", "mcp"])
    def test_runaway_skipped_for_one_shot_frameworks(self, fw):
        """Frameworks without a built-in loop don't need runaway —
        the user's own loop, if any, is invisible to us."""
        sel = select_packs(fw, _t("any_tool"))
        assert "sponsio:core/runaway" not in sel.packs

    @pytest.mark.parametrize(
        "tool_name,expected_canonical",
        [
            ("bash", "exec"),
            ("Bash", "exec"),  # lowercased on lookup
            ("execute_command", "exec"),
            ("run_command", "exec"),
            ("terminal", "exec"),
        ],
    )
    def test_shell_pack_via_alias(self, tool_name, expected_canonical):
        sel = select_packs("openai", _t(tool_name))
        assert "sponsio:capability/shell" in sel.packs
        assert sel.tool_rename == {expected_canonical: tool_name}

    def test_shell_pack_skipped_when_no_shell_tool(self):
        sel = select_packs("openai", _t("lookup_order", "send_email"))
        assert "sponsio:capability/shell" not in sel.packs

    def test_no_rename_when_tool_already_canonical(self):
        """If the host happens to call its tool ``exec`` (matching the
        pack), no rename is emitted — adding ``{exec: exec}`` would
        be a no-op and parser-rejected."""
        sel = select_packs("openai", _t("exec"))
        assert "sponsio:capability/shell" in sel.packs
        assert sel.tool_rename == {}

    @pytest.mark.parametrize(
        "tool_name,expected_canonical",
        [
            ("read_file", "read"),
            ("read", "read"),
            ("write_file", "write"),
            ("edit_file", "edit"),
            ("apply_patch", "apply_patch"),
            ("save_file", "write"),
        ],
    )
    def test_fs_pack_via_alias(self, tool_name, expected_canonical):
        sel = select_packs("openai", _t(tool_name))
        assert "sponsio:capability/filesystem" in sel.packs
        if tool_name != expected_canonical:
            assert sel.tool_rename[expected_canonical] == tool_name

    def test_fs_pack_sets_needs_workspace(self):
        """Filesystem pack uses ``<workspace>/`` placeholders, so the
        host config must set ``workspace:``.  Bundling the signal with
        the pack pick saves the caller from reasoning about which
        packs touch which features."""
        sel = select_packs("openai", _t("read_file"))
        assert sel.needs_workspace is True

    def test_combined_shell_and_fs(self):
        """A typical coding-agent project has both shell and fs tools
        — the heuristic should pick both packs and combine renames in
        one dict."""
        sel = select_packs("langgraph", _t("bash", "read_file", "write_file"))
        assert sel.packs == [
            "sponsio:core/universal",
            "sponsio:core/runaway",
            "sponsio:capability/shell",
            "sponsio:capability/filesystem",
        ]
        assert sel.tool_rename == {
            "exec": "bash",
            "read": "read_file",
            "write": "write_file",
        }

    def test_first_match_wins_for_alias(self):
        """Two tools that both alias to the same canonical name —
        pack rules can only target one tool at a time, so we take
        the first match (deterministic order beats picking randomly).
        Documented so users with multiple shell-like tools know what
        to expect."""
        sel = select_packs("openai", _t("bash", "terminal"))
        assert sel.tool_rename == {"exec": "bash"}

    def test_empty_inventory(self):
        """No tools — only the universal pack survives.  Runaway is
        skipped because the framework gate doesn't fire (one-shot)
        and capability packs need tool evidence to fire."""
        sel = select_packs("openai", [])
        assert sel.packs == ["sponsio:core/universal"]

    def test_none_inventory(self):
        """Defensive — CodeAnalyzer historically returns ``[]`` but
        treat None the same."""
        sel = select_packs("openai", None)
        assert sel.packs == ["sponsio:core/universal"]

    def test_evidence_one_per_pack(self):
        """Each pack pick gets a one-line reason — stored on the
        selection so the onboard banner and ``--json`` output can
        explain *why* each pack was picked.  Pin the contract."""
        sel = select_packs("langgraph", _t("bash", "read_file"))
        assert len(sel.evidence) == len(sel.packs)


# ---------------------------------------------------------------------------
# 2. _emit_pack_block + _splice_pack_block_into_agent — YAML rendering
# ---------------------------------------------------------------------------


class TestEmitPackBlock:
    def test_empty_selection_returns_no_lines(self):
        assert _emit_pack_block(PackSelection(), None) == []

    def test_workspace_emitted_only_when_needed(self):
        """Even if ``workspace`` is supplied, don't emit it for
        selections that don't include the filesystem pack — would
        be confusing dead config."""
        sel = PackSelection(packs=["sponsio:core/universal"])
        lines = _emit_pack_block(sel, workspace="/proj")
        assert not any("workspace:" in ln for ln in lines)

    def test_workspace_emitted_when_fs_pack_selected(self):
        sel = PackSelection(
            packs=["sponsio:capability/filesystem"],
            evidence=["filesystem"],
            needs_workspace=True,
        )
        lines = _emit_pack_block(sel, workspace="/proj")
        assert any('workspace: "/proj"' in ln for ln in lines)

    def test_tool_rename_block_emitted(self):
        sel = PackSelection(
            packs=["sponsio:capability/shell"],
            evidence=["shell"],
            tool_rename={"exec": "bash"},
        )
        lines = _emit_pack_block(sel, workspace=None)
        assert "    tool_rename:" in lines
        assert "      exec: bash" in lines

    def test_pack_evidence_appears_as_inline_comment(self):
        """Per-pack reason comments are how users understand the
        picks without reading docs.  Pin format: ``- pack  # why``."""
        sel = PackSelection(
            packs=["sponsio:core/universal"],
            evidence=["because PII"],
        )
        lines = _emit_pack_block(sel, workspace=None)
        assert any(
            "- sponsio:core/universal" in ln and "because PII" in ln for ln in lines
        )


class TestSplicePackBlock:
    def test_splice_into_existing_yaml(self):
        """The pack block lands directly under the agent header, before
        ``contracts:``, so all agent-scoped settings cluster
        together."""
        scan_yaml = textwrap.dedent("""\
            agents:
              bot:
                contracts:
                  - E:
                      pattern: rate_limit
                      args: [exec, 5]
            """)
        result = _splice_pack_block_into_agent(
            scan_yaml,
            agent_id="bot",
            pack_lines=["    include:", "      - sponsio:core/universal", ""],
        )
        # include: appears between the agent header and contracts:
        agent_idx = result.index("  bot:")
        include_idx = result.index("include:")
        contracts_idx = result.index("contracts:")
        assert agent_idx < include_idx < contracts_idx

    def test_no_op_when_pack_lines_empty(self):
        scan_yaml = "agents:\n  bot:\n    contracts: []\n"
        assert _splice_pack_block_into_agent(scan_yaml, "bot", []) == scan_yaml

    def test_no_op_when_agent_not_found(self):
        """The splice never modifies the YAML if the named agent
        doesn't exist — better to leave the file untouched than
        produce malformed output."""
        scan_yaml = "agents:\n  other:\n    contracts: []\n"
        out = _splice_pack_block_into_agent(
            scan_yaml,
            agent_id="bot",
            pack_lines=["    include: []"],
        )
        assert out == scan_yaml


# ---------------------------------------------------------------------------
# 3. End-to-end: run_onboard produces a config that loads cleanly
# ---------------------------------------------------------------------------


@pytest.fixture
def langgraph_project_with_capabilities(tmp_path: Path) -> Path:
    """A LangGraph-flavored project whose tools resemble shell + fs.
    Triggers all four packs and exercises the full propagation path:
    detection → selection → YAML emission → load_config."""
    src = tmp_path / "agent.py"
    src.write_text(
        textwrap.dedent("""\
        from langgraph.prebuilt import create_react_agent
        from langchain_core.tools import tool

        @tool
        def bash(cmd: str) -> str:
            \"\"\"Run shell.\"\"\"
            return ''

        @tool
        def read_file(path: str) -> str:
            return ''

        @tool
        def write_file(path: str, content: str) -> None:
            pass

        agent = create_react_agent(model, [bash, read_file, write_file])
        """)
    )
    return tmp_path


class TestRunOnboardAutoSelect:
    def test_report_carries_pack_selection(self, langgraph_project_with_capabilities):
        out = run_onboard(
            langgraph_project_with_capabilities,
            probe_ollama=False,
            run_doctor=False,
            force=True,
        )
        assert out.pack_selection is not None
        assert "sponsio:core/universal" in out.pack_selection.packs
        assert "sponsio:core/runaway" in out.pack_selection.packs
        assert "sponsio:capability/shell" in out.pack_selection.packs
        assert "sponsio:capability/filesystem" in out.pack_selection.packs

    def test_renames_pre_populated_from_inventory(
        self, langgraph_project_with_capabilities
    ):
        out = run_onboard(
            langgraph_project_with_capabilities,
            probe_ollama=False,
            run_doctor=False,
            force=True,
        )
        assert out.pack_selection.tool_rename == {
            "exec": "bash",
            "read": "read_file",
            "write": "write_file",
        }

    def test_emitted_yaml_loads_cleanly(self, langgraph_project_with_capabilities):
        """The strongest signal that auto-selection integrates with
        the rest of the loader — a project that uses shell + fs tools
        and runs the onboard command should land in a state where
        load_config succeeds end-to-end with a populated contracts
        list."""
        run_onboard(
            langgraph_project_with_capabilities,
            probe_ollama=False,
            run_doctor=False,
            force=True,
        )
        cfg = load_config(langgraph_project_with_capabilities / "sponsio.yaml")
        # Significant rule count — universal+runaway+shell+filesystem
        # ship dozens of contracts each, plus the scan-extracted
        # contracts.  Pin a lower bound that catches "no packs got
        # included" regressions without being brittle to pack churn.
        assert len(cfg.agents["agent"].contracts) > 30

    def test_workspace_resolves_in_emitted_yaml(
        self, langgraph_project_with_capabilities
    ):
        """The workspace placeholder gets resolved in pulled-in
        contracts at load time — the proof that the
        ``workspace: ...`` we emit is the right path."""
        out = run_onboard(
            langgraph_project_with_capabilities,
            probe_ollama=False,
            run_doctor=False,
            force=True,
        )
        assert out.pack_selection.needs_workspace
        cfg = load_config(langgraph_project_with_capabilities / "sponsio.yaml")
        # Find any contract from the filesystem pack with a path arg
        # and confirm <workspace>/ was substituted.
        fs_contracts = [
            c
            for c in cfg.agents["agent"].contracts
            if c.pack_source == "sponsio:capability/filesystem"
        ]
        assert fs_contracts, "filesystem pack should have contributed contracts"
        # At least one fs contract has args mentioning the resolved root
        ws = str(langgraph_project_with_capabilities.resolve())
        any_resolved = False
        for c in fs_contracts:
            e = (
                c.enforcement
                if not isinstance(c.enforcement, list)
                else c.enforcement[0]
            )
            for a in e.args or []:
                if isinstance(a, list):
                    for s in a:
                        if isinstance(s, str) and s.startswith(ws):
                            any_resolved = True
        assert any_resolved, (
            "expected at least one fs contract arg to start with the resolved workspace path"
        )

    def test_to_dict_includes_packs_section(self, langgraph_project_with_capabilities):
        """``--json`` shape: callers reading the JSON output (CI
        pipelines, IDE integrations) need the pack picks visible
        without re-running the heuristic themselves."""
        out = run_onboard(
            langgraph_project_with_capabilities,
            probe_ollama=False,
            run_doctor=False,
            force=True,
        )
        d = out.to_dict()
        assert "packs" in d
        assert d["packs"]["needs_workspace"] is True
        assert "sponsio:core/universal" in d["packs"]["selected"]
        assert d["packs"]["tool_rename"]["exec"] == "bash"
