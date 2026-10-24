"""Monotone layering (D5, P1.3): a project layer adds contracts to a
protected library and cannot weaken it."""

from __future__ import annotations

from pathlib import Path

import pytest

from sponsio.config import ConfigError, load_config, load_layered_config
from sponsio.daemon.integrity import agent_ruleset

AGENT = "_host_claude_code"

PROTECTED = f"""
version: "1"
defaults:
  unconfigured: deny
agents:
  {AGENT}:
    include:
      - sponsio:capability/shell
    contracts:
      - desc: "no rm -rf"
        G:
          pattern: arg_blacklist
          args: [Bash, command, ["rm\\\\s+-rf"]]
"""


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _descs(config, agent=AGENT) -> set[str]:
    return {c.desc for c in config.agents[agent].contracts if c.desc}


def test_the_effective_set_is_protected_plus_project(tmp_path):
    protected = _write(tmp_path, "protected/sponsio.yaml", PROTECTED)
    project_text = f"""
version: "1"
agents:
  {AGENT}:
    contracts:
      - desc: "at most 5 Bash calls"
        G: {{pattern: rate_limit, args: [Bash, 5]}}
"""
    project = _write(tmp_path, "repo/sponsio.yaml", project_text)
    effective, dropped = load_layered_config(protected, project)
    assert dropped == []
    want = dict(agent_ruleset(load_config(protected), AGENT))
    want.update(agent_ruleset(load_config(project), AGENT))
    assert agent_ruleset(effective, AGENT) == want
    layers = {c.desc: c.layer for c in effective.agents[AGENT].contracts}
    assert layers["no rm -rf"] == "protected"
    assert layers["at most 5 Bash calls"] == "project"
    assert str(project.resolve()) in effective.source_files


def test_a_project_layer_cannot_disable_a_protected_contract(tmp_path):
    protected = _write(tmp_path, "protected/sponsio.yaml", PROTECTED)
    project = _write(
        tmp_path,
        "repo/sponsio.yaml",
        f"""
version: "1"
agents:
  {AGENT}:
    customized:
      - match: {{desc: "no rm -rf"}}
        disabled: true
      - match: {{pack_source: "sponsio:capability/shell"}}
        disabled: true
""",
    )
    effective, dropped = load_layered_config(protected, project)
    assert _descs(effective) == _descs(load_config(protected))
    assert len(dropped) == 2 and all("protected contract" in d for d in dropped)


def test_a_project_layer_can_customize_its_own_contracts(tmp_path):
    protected = _write(tmp_path, "protected/sponsio.yaml", PROTECTED)
    project = _write(
        tmp_path,
        "repo/sponsio.yaml",
        f"""
version: "1"
agents:
  {AGENT}:
    contracts:
      - desc: "mine"
        G: {{pattern: rate_limit, args: [Bash, 5]}}
      - desc: "also mine"
        G: {{pattern: rate_limit, args: [Read, 5]}}
    customized:
      - match: {{desc: "also mine"}}
        disabled: true
""",
    )
    effective, dropped = load_layered_config(protected, project)
    assert dropped == []
    assert "mine" in _descs(effective) and "also mine" not in _descs(effective)


def test_project_settings_that_change_every_decision_are_ignored(tmp_path):
    protected = _write(tmp_path, "protected/sponsio.yaml", PROTECTED)
    project = _write(
        tmp_path,
        "repo/sponsio.yaml",
        f"""
version: "1"
mode: observe
defaults:
  unconfigured: allow
tool_policy:
  default: allow
agents:
  {AGENT}:
    contracts: []
""",
    )
    effective, dropped = load_layered_config(protected, project)
    assert effective.defaults == {"unconfigured": "deny"}
    assert effective.top_level_mode is None
    assert {d.split("`")[1] for d in dropped} == {"mode", "defaults", "tool_policy"}


def test_an_unmatched_project_customization_is_still_an_error(tmp_path):
    protected = _write(tmp_path, "protected/sponsio.yaml", PROTECTED)
    project = _write(
        tmp_path,
        "repo/sponsio.yaml",
        f"""
version: "1"
agents:
  {AGENT}:
    customized:
      - match: {{desc: "no such rule"}}
        disabled: true
""",
    )
    with pytest.raises(ConfigError, match="matched no contract"):
        load_layered_config(protected, project)


def test_a_project_only_agent_is_added(tmp_path):
    protected = _write(tmp_path, "protected/sponsio.yaml", PROTECTED)
    project = _write(
        tmp_path,
        "repo/sponsio.yaml",
        """
version: "1"
agents:
  helper:
    contracts:
      - desc: "helper limit"
        G: {pattern: rate_limit, args: [Bash, 2]}
""",
    )
    effective, _ = load_layered_config(protected, project)
    assert set(effective.agents) == {AGENT, "helper"}
    assert {c.layer for c in effective.agents["helper"].contracts} == {"project"}


def test_the_layer_is_not_part_of_a_contract_id(tmp_path):
    """Approved rulesets on disk and the cloud's comparison hold ids;
    tagging layers must not move them."""
    from sponsio.daemon.integrity import ruleset

    cfg = load_config(_write(tmp_path, "protected/sponsio.yaml", PROTECTED))
    before = (ruleset(cfg), agent_ruleset(cfg, AGENT))
    for c in cfg.agents[AGENT].contracts:
        c.layer = "protected"
    assert (ruleset(cfg), agent_ruleset(cfg, AGENT)) == before
