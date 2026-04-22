"""Tests for YAML parsing of alpha/beta/risk_profile/costs threshold specs."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from sponsio.config import ConfigError, config_to_system, load_config


def _write_yaml(content: str) -> Path:
    fd = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    fd.write(content)
    fd.close()
    return Path(fd.name)


class TestDefaultAlphaBeta:
    def test_no_thresholds_defaults_to_1_1(self):
        path = _write_yaml(
            """
version: "1"
agents:
  bot:
    contracts:
      - E: "tool `A` must precede `B`"
"""
        )
        config = load_config(path)
        ce = config.agents["bot"].contracts[0]
        assert ce.alpha == 1.0
        assert ce.beta == 1.0


class TestExplicitAlphaBeta:
    def test_explicit_alpha_beta(self):
        path = _write_yaml(
            """
version: "1"
agents:
  bot:
    contracts:
      - A: "called `web_search`"
        E: "response must not echo injection"
        alpha: 0.7
        beta: 0.95
"""
        )
        config = load_config(path)
        ce = config.agents["bot"].contracts[0]
        assert ce.alpha == 0.7
        assert ce.beta == 0.95

    def test_only_beta_set_alpha_defaults(self):
        path = _write_yaml(
            """
version: "1"
agents:
  bot:
    contracts:
      - E: "response must not contain PII"
        beta: 0.95
"""
        )
        config = load_config(path)
        ce = config.agents["bot"].contracts[0]
        assert ce.alpha == 1.0
        assert ce.beta == 0.95


class TestRiskProfile:
    def test_cautious(self):
        path = _write_yaml(
            """
version: "1"
agents:
  bot:
    contracts:
      - E: "response must be on-topic"
        risk_profile: cautious
"""
        )
        config = load_config(path)
        ce = config.agents["bot"].contracts[0]
        assert ce.alpha == 0.7
        assert ce.beta == 0.95

    def test_strict_compliance(self):
        path = _write_yaml(
            """
version: "1"
agents:
  bot:
    contracts:
      - E: "response must not leak PHI"
        risk_profile: strict_compliance
"""
        )
        config = load_config(path)
        ce = config.agents["bot"].contracts[0]
        assert ce.alpha == 0.6
        assert ce.beta == 0.999

    def test_unknown_profile_raises_configerror(self):
        path = _write_yaml(
            """
version: "1"
agents:
  bot:
    contracts:
      - E: "..."
        risk_profile: paranoid
"""
        )
        with pytest.raises(ConfigError, match="unknown risk_profile"):
            load_config(path)


class TestCosts:
    def test_costs_derive_beta(self):
        path = _write_yaml(
            """
version: "1"
agents:
  bot:
    contracts:
      - E: "response must be on-topic"
        costs: {fp: 1, fn: 20}
"""
        )
        config = load_config(path)
        ce = config.agents["bot"].contracts[0]
        assert ce.beta == pytest.approx(20 / 21)
        assert ce.alpha == 0.7  # default when atom_category absent

    def test_malformed_costs_raises_configerror(self):
        path = _write_yaml(
            """
version: "1"
agents:
  bot:
    contracts:
      - E: "..."
        costs: {fn: 20}
"""
        )
        with pytest.raises(ConfigError, match="fp|fn"):
            load_config(path)


class TestSpecConflict:
    def test_alpha_and_risk_profile_conflict(self):
        path = _write_yaml(
            """
version: "1"
agents:
  bot:
    contracts:
      - E: "..."
        alpha: 0.7
        risk_profile: cautious
"""
        )
        with pytest.raises(ConfigError, match="ambiguous"):
            load_config(path)

    def test_costs_and_beta_conflict(self):
        path = _write_yaml(
            """
version: "1"
agents:
  bot:
    contracts:
      - E: "..."
        beta: 0.95
        costs: {fp: 1, fn: 20}
"""
        )
        with pytest.raises(ConfigError, match="ambiguous"):
            load_config(path)


class TestThresholdPropagatesToContract:
    def test_config_to_system_sets_alpha_beta(self):
        path = _write_yaml(
            """
version: "1"
agents:
  bot:
    contracts:
      - E: "tool `A` must precede `B`"
        risk_profile: cautious
"""
        )
        config = load_config(path)
        system = config_to_system(config)
        contracts = list(system._contracts)
        assert len(contracts) == 1
        c = contracts[0]
        assert c.alpha == 0.7
        assert c.beta == 0.95

    def test_default_contract_has_1_1_thresholds(self):
        path = _write_yaml(
            """
version: "1"
agents:
  bot:
    contracts:
      - E: "tool `A` must precede `B`"
"""
        )
        config = load_config(path)
        system = config_to_system(config)
        c = list(system._contracts)[0]
        assert c.alpha == 1.0
        assert c.beta == 1.0
