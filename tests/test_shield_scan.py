"""Tests for ``sponsio shield scan`` and the underlying scanner module.

Validates three layers:

1. **Manifest parsing** (:mod:`sponsio.shield.scan`) — given a Claude
   Code plugin directory, we extract the plugin id + MCP server
   inventory + skill list, and reject malformed inputs early.

2. **Library generation** — for each declared tool name we run the
   existing ``starter_pack`` heuristics and partition the rules by
   the same routing key the runtime hook uses
   (``derive_plugin_id``). One yaml file per group.

3. **CLI behaviour** — ``--tools`` parsing, dry-run vs ``--apply``,
   ``--force`` semantics, group-by-routed-id output layout.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from sponsio.shield.scan import (
    ManifestError,
    parse_plugin_manifest,
    scan_plugin,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Fixtures: build small plugin directories on the fly
# ---------------------------------------------------------------------------


def _make_plugin(
    tmp_path: Path,
    plugin_id: str,
    *,
    version: str = "1.0.0",
    description: str = "test plugin",
    mcp_servers: dict | None = None,
    skill_names: list[str] | None = None,
) -> Path:
    """Build a minimal valid Claude Code plugin layout under ``tmp_path``."""
    root = tmp_path / plugin_id
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": plugin_id, "version": version, "description": description})
    )
    if mcp_servers:
        (root / ".mcp.json").write_text(json.dumps({"mcpServers": mcp_servers}))
    for skill in skill_names or []:
        (root / "skills" / skill).mkdir(parents=True)
        (root / "skills" / skill / "SKILL.md").write_text(
            "---\nname: " + skill + "\ndescription: x\n---\nbody"
        )
    return root


# ---------------------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------------------


def test_parse_minimal_plugin(tmp_path):
    p = _make_plugin(tmp_path, "my-plugin", version="2.3.4")
    m = parse_plugin_manifest(p)
    assert m.plugin_id == "my-plugin"
    assert m.version == "2.3.4"
    assert m.mcp_servers == []
    assert m.skill_names == []


def test_parse_collects_mcp_servers_and_skills(tmp_path):
    p = _make_plugin(
        tmp_path,
        "rich",
        mcp_servers={"github": {"command": "npx", "args": ["-y", "x"]}, "fs": {}},
        skill_names=["scan", "review"],
    )
    m = parse_plugin_manifest(p)
    assert sorted(m.mcp_servers) == ["fs", "github"]
    assert m.skill_names == ["review", "scan"]


def test_parse_rejects_missing_manifest(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    with pytest.raises(ManifestError, match="No .claude-plugin/plugin.json"):
        parse_plugin_manifest(bare)


def test_parse_rejects_invalid_json(tmp_path):
    p = tmp_path / "bad"
    (p / ".claude-plugin").mkdir(parents=True)
    (p / ".claude-plugin" / "plugin.json").write_text("{not json")
    with pytest.raises(ManifestError, match="Invalid plugin.json"):
        parse_plugin_manifest(p)


def test_parse_rejects_missing_name(tmp_path):
    p = tmp_path / "noname"
    (p / ".claude-plugin").mkdir(parents=True)
    (p / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": "1"}))
    with pytest.raises(ManifestError, match="non-empty `name`"):
        parse_plugin_manifest(p)


def test_parse_tolerates_malformed_mcp_json(tmp_path):
    """Don't fail the whole scan over a typo'd .mcp.json."""
    p = _make_plugin(tmp_path, "tolerant")
    (p / ".mcp.json").write_text("{bad")
    m = parse_plugin_manifest(p)
    assert m.plugin_id == "tolerant"
    assert m.mcp_servers == []


# ---------------------------------------------------------------------------
# scan_plugin: partitioning by routed plugin_id
# ---------------------------------------------------------------------------


def test_scan_partitions_tools_by_routed_id(tmp_path):
    """Tool names route to different ``plugin_id`` groups; each gets its
    own ``LibraryGroup``."""
    p = _make_plugin(tmp_path, "host-plugin")
    result = scan_plugin(
        p,
        declared_tools=[
            "Bash",
            "mcp__github__delete_repo",
            "mcp__github__create_issue",
            "mcp__filesystem__write_file",
            "acme:fetch",
        ],
    )
    ids = sorted(g.plugin_id for g in result.groups)
    assert ids == ["_host", "acme", "filesystem", "github"]

    by_id = {g.plugin_id: g for g in result.groups}
    assert by_id["_host"].tools == ["Bash"]
    assert by_id["github"].tools == [
        "mcp__github__delete_repo",
        "mcp__github__create_issue",
    ]
    assert by_id["filesystem"].tools == ["mcp__filesystem__write_file"]
    assert by_id["acme"].tools == ["acme:fetch"]


def test_scan_baseline_only_when_no_tools(tmp_path):
    """No tools → one group keyed by the plugin id with just the runaway include."""
    p = _make_plugin(tmp_path, "baseline-plugin")
    result = scan_plugin(p, declared_tools=[])
    assert len(result.groups) == 1
    g = result.groups[0]
    assert g.plugin_id == "baseline-plugin"
    assert g.tools == []
    assert g.proposed == []
    parsed = yaml.safe_load(g.library_yaml)
    assert parsed["agents"]["baseline-plugin"]["include"] == ["sponsio:core/runaway"]


def test_scan_emits_heuristic_rules_per_tool(tmp_path):
    p = _make_plugin(tmp_path, "p")
    result = scan_plugin(p, declared_tools=["mcp__github__delete_repo"])
    g = next(g for g in result.groups if g.plugin_id == "github")
    pattern_names = sorted({pc.formula.pattern_name for pc in g.proposed})
    # Heuristic must spot the destructive verb + add the always-on
    # consecutive-call cap.
    assert "irreversible_once" in pattern_names
    assert "loop_detection" in pattern_names


def test_scan_no_runaway_omits_include(tmp_path):
    p = _make_plugin(tmp_path, "p")
    result = scan_plugin(p, declared_tools=["Bash"], include_runaway=False)
    g = next(g for g in result.groups if g.plugin_id == "_host")
    parsed = yaml.safe_load(g.library_yaml)
    block = parsed["agents"]["_host"]
    assert "include" not in block


# ---------------------------------------------------------------------------
# Generated yaml is loadable + functionally correct
# ---------------------------------------------------------------------------


def test_generated_yaml_is_loadable_via_baseguard(tmp_path, monkeypatch):
    """The produced library must be readable by the runtime config
    loader. If a future change to ``starter_pack`` produces an arg
    shape that the YAML round-trip can't restore, this test fires."""
    p = _make_plugin(tmp_path, "p")
    result = scan_plugin(
        p, declared_tools=["mcp__github__delete_repo"], include_runaway=False
    )
    g = next(g for g in result.groups if g.plugin_id == "github")

    target_dir = tmp_path / "_root" / "github"
    target_dir.mkdir(parents=True)
    target = target_dir / "sponsio.yaml"
    target.write_text(g.library_yaml)

    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path / "_root"))
    monkeypatch.delenv("SPONSIO_GUARD_MODE", raising=False)

    from sponsio.guard_stdin import evaluate_event

    outcome = evaluate_event(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "mcp__github__list_repos",  # not in proposed → allowed
            "tool_input": {},
        }
    )
    assert outcome.plugin_id == "github"
    assert outcome.allowed is True


# ---------------------------------------------------------------------------
# CLI: dry-run, --apply, --force
# ---------------------------------------------------------------------------


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "sponsio.cli", "shield", "scan", *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_cli_dry_run_prints_each_group(tmp_path):
    p = _make_plugin(tmp_path, "p")
    proc = _run_cli(
        str(p),
        "--tools",
        "Bash,mcp__github__delete_repo",
    )
    assert proc.returncode == 0, proc.stderr
    assert "_host" in proc.stdout
    assert "github" in proc.stdout
    assert "library group: _host" in proc.stdout
    assert "library group: github" in proc.stdout


def test_cli_apply_writes_one_file_per_group(tmp_path):
    p = _make_plugin(tmp_path, "p")
    root = tmp_path / "out"
    proc = _run_cli(
        str(p),
        "--tools",
        "Bash,mcp__github__delete_repo",
        "--root",
        str(root),
        "--apply",
    )
    assert proc.returncode == 0, proc.stderr
    assert (root / "_host" / "sponsio.yaml").exists()
    assert (root / "github" / "sponsio.yaml").exists()


def test_cli_apply_refuses_overwrite_without_force(tmp_path):
    p = _make_plugin(tmp_path, "p")
    root = tmp_path / "out"
    target = root / "_host" / "sponsio.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("# existing user file")

    proc = _run_cli(
        str(p),
        "--tools",
        "Bash",
        "--root",
        str(root),
        "--apply",
    )
    # All groups skipped (the only one was _host) → exit 1.
    assert proc.returncode == 1
    assert target.read_text() == "# existing user file"


def test_cli_apply_force_overwrites(tmp_path):
    p = _make_plugin(tmp_path, "p")
    root = tmp_path / "out"
    target = root / "_host" / "sponsio.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("# existing user file")

    proc = _run_cli(
        str(p),
        "--tools",
        "Bash",
        "--root",
        str(root),
        "--apply",
        "--force",
    )
    assert proc.returncode == 0, proc.stderr
    # File was overwritten; must contain the heuristic rules now.
    new_content = target.read_text()
    assert "shield-scan" in new_content
    assert "Bash" in new_content


def test_cli_rejects_non_plugin_dir(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    proc = _run_cli(str(bare), "--tools", "Bash")
    assert proc.returncode == 1
    assert "scan failed" in proc.stdout or "scan failed" in proc.stderr
