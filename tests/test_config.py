"""Tests for YAML config loader."""

from __future__ import annotations

import pytest

from sponsio.config import (
    ConfigError,
    config_to_guard_kwargs,
    config_to_system,
    load_config,
)


@pytest.fixture
def full_config(tmp_path):
    f = tmp_path / "sponsio.yaml"
    f.write_text(
        """
version: "1"
defaults:
  verbose: true
  verbosity: 2
agents:
  bot:
    contracts:
      - A: "tool `check_policy` must precede `issue_refund`"
        E: "tool `issue_refund` at most 1 times"
      - E: "response must not contain PII"
"""
    )
    return f


@pytest.fixture
def bare_config(tmp_path):
    f = tmp_path / "sponsio.yaml"
    f.write_text(
        """
agents:
  simple:
    - "tool `A` must precede `B`"
    - "tool `X` at most 3 times"
"""
    )
    return f


@pytest.fixture
def multi_agent_config(tmp_path):
    f = tmp_path / "sponsio.yaml"
    f.write_text(
        """
version: "1"
agents:
  planner:
    contracts:
      - E: "tool `plan` must precede `execute`"
  executor:
    contracts:
      - A: "tool `plan` must precede `execute`"
        E: "tool `execute` at most 5 times"
"""
    )
    return f


@pytest.fixture
def and_list_config(tmp_path):
    f = tmp_path / "sponsio.yaml"
    f.write_text(
        """
agents:
  bot:
    contracts:
      - A:
          - "tool `authn` must precede `act`"
          - "tool `authz` must precede `act`"
        E:
          - "tool `act` at most 2 times"
          - "tool `act` must precede `finalize`"
"""
    )
    return f


def test_load_full_config(full_config):
    config = load_config(full_config)
    assert config.version == "1"
    assert "bot" in config.agents
    bot = config.agents["bot"]
    assert len(bot.contracts) == 2
    # First contract: assumption + enforcement
    c0 = bot.contracts[0]
    assert c0.assumption is not None
    assert "check_policy" in c0.assumption.nl
    # Second contract: enforcement only (unconditional)
    c1 = bot.contracts[1]
    assert c1.assumption is None


def test_load_bare_config(bare_config):
    config = load_config(bare_config)
    agent = config.agents["simple"]
    assert len(agent.contracts) == 2
    for ce in agent.contracts:
        assert ce.assumption is None


def test_config_to_guard_kwargs(full_config):
    config = load_config(full_config)
    kwargs = config_to_guard_kwargs(config, "bot")
    assert kwargs["agent_id"] == "bot"
    assert len(kwargs["contracts"]) == 2
    assert kwargs.get("verbosity") == 2


def test_config_to_guard_kwargs_missing_agent(full_config):
    config = load_config(full_config)
    with pytest.raises(ConfigError, match="not found"):
        config_to_guard_kwargs(config, "nonexistent")


def test_config_to_system(multi_agent_config):
    config = load_config(multi_agent_config)
    system = config_to_system(config)
    # Each agent contributes one Contract
    assert len(system.contracts) == 2
    agent_ids = {c.agent.id for c in system.contracts}
    assert agent_ids == {"planner", "executor"}


def test_load_nonexistent():
    with pytest.raises(FileNotFoundError):
        load_config("/tmp/does_not_exist_sponsio.yaml")


def test_load_invalid_yaml(tmp_path):
    f = tmp_path / "bad.yaml"
    f.write_text("{{invalid yaml::")
    with pytest.raises(ConfigError, match="Invalid YAML"):
        load_config(f)


def test_load_non_dict(tmp_path):
    f = tmp_path / "list.yaml"
    f.write_text("- item1\n- item2\n")
    with pytest.raises(ConfigError, match="must be a YAML mapping"):
        load_config(f)


def test_load_invalid_agent_value(tmp_path):
    f = tmp_path / "bad_agent.yaml"
    f.write_text(
        """
agents:
  bot: "just a string"
"""
    )
    with pytest.raises(ConfigError, match="must be a mapping or list"):
        load_config(f)


def test_yaml_accepts_long_name_keys(tmp_path):
    """Long keys ``assumption`` / ``enforcement`` are accepted in YAML."""
    f = tmp_path / "fullname.yaml"
    f.write_text(
        """
agents:
  bot:
    contracts:
      - assumption: "tool `A` must precede `B`"
        enforcement: "tool `X` at most 3 times"
"""
    )
    config = load_config(f)
    bot = config.agents["bot"]
    assert len(bot.contracts) == 1
    c0 = bot.contracts[0]
    assert c0.assumption is not None
    assert "tool `A`" in c0.assumption.nl
    assert "at most 3" in c0.enforcement.nl


def test_yaml_mixed_short_and_long_keys_across_entries(tmp_path):
    """Short and long keys may coexist in the same file across entries."""
    f = tmp_path / "mixed.yaml"
    f.write_text(
        """
agents:
  bot:
    contracts:
      - A: "tool `X` must precede `Y`"
        E: "tool `Y` at most 1 times"
      - assumption: "tool `Z` must precede `W`"
        enforcement: "tool `W` at most 2 times"
      - E: "response must not contain PII"
      - enforcement: "tool `sed` arg contains `-i` is banned"
"""
    )
    config = load_config(f)
    bot = config.agents["bot"]
    assert len(bot.contracts) == 4
    assert bot.contracts[0].assumption is not None
    assert bot.contracts[1].assumption is not None
    assert bot.contracts[2].assumption is None
    assert bot.contracts[3].assumption is None


def test_yaml_rejects_conflicting_short_and_long_assumption(tmp_path):
    """Using both ``A`` and ``assumption`` in one entry is ambiguous."""
    f = tmp_path / "conflict_a.yaml"
    f.write_text(
        """
agents:
  bot:
    contracts:
      - A: "tool `X` must precede `Y`"
        assumption: "tool `X` must precede `Y`"
        E: "tool `Y` at most 1 times"
"""
    )
    with pytest.raises(ConfigError, match="both 'A' and 'assumption'"):
        load_config(f)


def test_yaml_rejects_conflicting_short_and_long_enforcement(tmp_path):
    """Using both ``E`` and ``enforcement`` in one entry is ambiguous."""
    f = tmp_path / "conflict_e.yaml"
    f.write_text(
        """
agents:
  bot:
    contracts:
      - E: "tool `Y` at most 1 times"
        enforcement: "tool `Y` at most 1 times"
"""
    )
    with pytest.raises(ConfigError, match="both 'E' and 'enforcement'"):
        load_config(f)


def test_yaml_cross_form_fields_in_one_entry(tmp_path):
    """Mixing ``A`` with ``enforcement`` (or vice versa) is fine."""
    f = tmp_path / "cross.yaml"
    f.write_text(
        """
agents:
  bot:
    contracts:
      - A: "tool `X` must precede `Y`"
        enforcement: "tool `Y` at most 1 times"
      - assumption: "tool `Z` must precede `W`"
        E: "tool `W` at most 2 times"
"""
    )
    config = load_config(f)
    bot = config.agents["bot"]
    assert len(bot.contracts) == 2
    assert bot.contracts[0].assumption is not None
    assert bot.contracts[1].assumption is not None


def test_old_schema_rejected(tmp_path):
    f = tmp_path / "old.yaml"
    f.write_text(
        """
agents:
  bot:
    assumptions:
      - "tool `A` must precede `B`"
    guarantees:
      - "tool `X` at most 3 times"
"""
    )
    with pytest.raises(ConfigError, match="no longer supported"):
        load_config(f)


def test_defaults_applied(full_config):
    config = load_config(full_config)
    assert config.defaults["verbose"] is True
    assert config.defaults["verbosity"] == 2


def test_enforcement_only_agent(tmp_path):
    f = tmp_path / "g_only.yaml"
    f.write_text(
        """
agents:
  bot:
    contracts:
      - E: "tool `X` at most 3 times"
"""
    )
    config = load_config(f)
    bot = config.agents["bot"]
    assert len(bot.contracts) == 1
    assert bot.contracts[0].assumption is None


def test_and_list_assumption_and_enforcement(and_list_config):
    config = load_config(and_list_config)
    bot = config.agents["bot"]
    assert len(bot.contracts) == 1
    ce = bot.contracts[0]
    assert isinstance(ce.assumption, list)
    assert len(ce.assumption) == 2
    assert isinstance(ce.enforcement, list)
    assert len(ce.enforcement) == 2

    # Compiled through config_to_system, each contract is a single pair
    system = config_to_system(config)
    assert len(system.contracts) == 1
    c = system.contracts[0]
    assert isinstance(c.assumption, list)
    assert isinstance(c.enforcement, list)


def test_langgraph_guard_alias():
    from sponsio.integrations.langgraph import ContractGuard, LangGraphGuard

    assert ContractGuard is LangGraphGuard


def test_agents_guard_alias():
    from sponsio.integrations.agents import AgentsGuard, AgentsSDKGuard

    assert AgentsGuard is AgentsSDKGuard
