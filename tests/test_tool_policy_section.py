"""Tests for ``tool_policy:`` YAML section.

Locks in the v0.2 default-deny posture:

    tool_policy:
      default: deny
      approved: [search, read_file]
      enforcement: reactive    # reactive | proactive

* ``default: allow`` (the legacy default) injects no contract, so
  existing yaml files behave byte-for-byte the same.
* ``default: deny`` synthesizes a ``tool_allowlist`` det contract and
  prepends it to every agent's contracts list. An empty ``approved``
  under deny is honoured (locks the agent down completely).
* ``enforcement`` is parsed and validated now so the schema is stable;
  the proactive-filtering behaviour wires in via adapters later.
* Typos in ``default`` / ``enforcement`` raise ``ConfigError`` at load
  time rather than silently falling back to the default value.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("yaml")

from sponsio.config import (
    ConfigError,
    ToolPolicySection,
    _parse_tool_policy_section,
    _synthesize_tool_policy_contract,
    config_to_guard_kwargs,
    config_to_system,
    load_config,
)


# ---------------------------------------------------------------------------
# _parse_tool_policy_section. unit tests
# ---------------------------------------------------------------------------


class TestParseToolPolicySection:
    def test_none_returns_defaults(self) -> None:
        p = _parse_tool_policy_section(None)
        assert p == ToolPolicySection()
        assert p.default == "allow"
        assert p.approved == []
        assert p.enforcement == "reactive"

    def test_rejects_non_mapping(self) -> None:
        with pytest.raises(ConfigError, match="tool_policy"):
            _parse_tool_policy_section(["default: deny"])  # type: ignore[list-item]

    def test_default_deny(self) -> None:
        p = _parse_tool_policy_section({"default": "deny"})
        assert p.default == "deny"

    def test_default_allow_explicit(self) -> None:
        p = _parse_tool_policy_section({"default": "allow"})
        assert p.default == "allow"

    def test_default_rejects_typo(self) -> None:
        with pytest.raises(ConfigError, match="tool_policy.default"):
            _parse_tool_policy_section({"default": "deni"})

    def test_default_rejects_non_string(self) -> None:
        with pytest.raises(ConfigError, match="tool_policy.default"):
            _parse_tool_policy_section({"default": True})

    def test_approved_flat_list(self) -> None:
        p = _parse_tool_policy_section(
            {"default": "deny", "approved": ["search", "read_file"]}
        )
        assert p.approved == ["search", "read_file"]

    def test_approved_nested_form(self) -> None:
        """``approved: {tools: [...]}`` reserves room for per-host
        scoping later (``approved: {tools: [...], hosts: {...}}``)
        without breaking the flat form callers use today."""
        p = _parse_tool_policy_section(
            {"default": "deny", "approved": {"tools": ["search"]}}
        )
        assert p.approved == ["search"]

    def test_approved_rejects_non_string_entry(self) -> None:
        with pytest.raises(ConfigError, match=r"tool_policy\.approved\[1\]"):
            _parse_tool_policy_section({"default": "deny", "approved": ["search", 42]})

    def test_approved_rejects_empty_string_entry(self) -> None:
        with pytest.raises(ConfigError, match=r"tool_policy\.approved\[0\]"):
            _parse_tool_policy_section({"default": "deny", "approved": [""]})

    def test_approved_strips_whitespace(self) -> None:
        p = _parse_tool_policy_section({"default": "deny", "approved": ["  search  "]})
        assert p.approved == ["search"]

    def test_enforcement_reactive(self) -> None:
        p = _parse_tool_policy_section({"enforcement": "reactive"})
        assert p.enforcement == "reactive"

    def test_enforcement_proactive(self) -> None:
        p = _parse_tool_policy_section({"enforcement": "proactive"})
        assert p.enforcement == "proactive"

    def test_enforcement_rejects_typo(self) -> None:
        with pytest.raises(ConfigError, match="tool_policy.enforcement"):
            _parse_tool_policy_section({"enforcement": "proactiv"})


# ---------------------------------------------------------------------------
# _synthesize_tool_policy_contract. unit tests
# ---------------------------------------------------------------------------


class TestSynthesizeToolPolicyContract:
    def test_allow_returns_none(self) -> None:
        assert (
            _synthesize_tool_policy_contract(
                ToolPolicySection(default="allow", approved=["x"])
            )
            is None
        )

    def test_deny_with_approved_returns_contract(self) -> None:
        c = _synthesize_tool_policy_contract(
            ToolPolicySection(default="deny", approved=["search", "read_file"])
        )
        assert c is not None
        assert "search, read_file" in c["desc"]
        assert "default-deny" in c["desc"]
        # The compiled formula should be a DetFormula tagged
        # ``tool_allowlist`` so reports / dashboards group it correctly.
        formula = c["guarantee"]
        assert getattr(formula, "pattern_name", None) == "tool_allowlist"

    def test_deny_empty_approved_locks_down(self) -> None:
        """Empty allowlist + deny semantically means "block everything".
        The synthesized contract still gets built; the desc spells out
        the lockdown so the trace explanation is obvious when it fires."""
        c = _synthesize_tool_policy_contract(
            ToolPolicySection(default="deny", approved=[])
        )
        assert c is not None
        assert "no tools approved" in c["desc"]
        assert "everything blocked" in c["desc"]


# ---------------------------------------------------------------------------
# End-to-end YAML loading
# ---------------------------------------------------------------------------


def _write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "sponsio.yaml"
    p.write_text(body)
    return p


class TestYamlIntegration:
    def test_yaml_without_tool_policy_keeps_defaults(self, tmp_path: Path) -> None:
        """Existing yaml files (pre-v0.2) must keep working unchanged.
        The absence of a ``tool_policy:`` block resolves to allow + no
        injected contract."""
        p = _write_yaml(
            tmp_path,
            "agents:\n  bot:\n    contracts: []\n",
        )
        cfg = load_config(p)
        assert cfg.tool_policy.default == "allow"
        kwargs = config_to_guard_kwargs(cfg, "bot")
        # No contracts means nothing got injected.
        assert kwargs["contracts"] is None

    def test_yaml_default_deny_injects_contract(self, tmp_path: Path) -> None:
        """``config_to_guard_kwargs`` no longer prepends the deny
        contract directly. Synthesis lives in ``BaseGuard.__init__``
        so factory / direct-class / yaml entry paths behave
        identically. The guard built end-to-end via the Sponsio
        factory should still have the contract."""
        from sponsio.core import Sponsio

        p = _write_yaml(
            tmp_path,
            """
tool_policy:
  default: deny
  approved: [search, read_file]
agents:
  bot:
    contracts: []
""".strip()
            + "\n",
        )
        guard = Sponsio(config=str(p), agent_id="bot", verbose=False)
        contracts = guard._monitor._system.contracts
        assert len(contracts) == 1
        guarantee = contracts[0].guarantee
        assert getattr(guarantee, "pattern_name", None) == "tool_allowlist"
        assert "default-deny" in (contracts[0].desc or "")

    def test_injected_contract_is_first(self, tmp_path: Path) -> None:
        """Injection is prepended so the deny rule evaluates ahead of
        agent-authored contracts. Keeps the "first-line defense"
        framing tool_allowlist's docstring promises."""
        from sponsio.core import Sponsio

        p = _write_yaml(
            tmp_path,
            """
tool_policy:
  default: deny
  approved: [search]
agents:
  bot:
    contracts:
      - G: "tool `execute` at most 5 times"
""".strip()
            + "\n",
        )
        guard = Sponsio(config=str(p), agent_id="bot", verbose=False)
        contracts = guard._monitor._system.contracts
        assert len(contracts) == 2
        assert contracts[0].guarantee.pattern_name == "tool_allowlist"

    def test_default_allow_injects_nothing(self, tmp_path: Path) -> None:
        p = _write_yaml(
            tmp_path,
            """
tool_policy:
  default: allow
agents:
  bot:
    contracts:
      - G: "tool `execute` at most 5 times"
""".strip()
            + "\n",
        )
        cfg = load_config(p)
        kwargs = config_to_guard_kwargs(cfg, "bot")
        # Only the user's own contract survives.
        assert kwargs["contracts"] is not None
        assert len(kwargs["contracts"]) == 1
        assert kwargs["contracts"][0]["guarantee"].pattern_name != "tool_allowlist"

    def test_deny_propagates_into_system_per_agent(self, tmp_path: Path) -> None:
        """``config_to_system`` injects one tool_allowlist contract per
        agent so multi-agent configs get uniform default-deny coverage
        without the user repeating the rule under each agent."""
        p = _write_yaml(
            tmp_path,
            """
tool_policy:
  default: deny
  approved: [search]
agents:
  bot_a:
    contracts: []
  bot_b:
    contracts: []
""".strip()
            + "\n",
        )
        cfg = load_config(p)
        system = config_to_system(cfg)
        injected = [
            c
            for c in system._contracts
            if getattr(c.guarantee, "pattern_name", None) == "tool_allowlist"
        ]
        assert len(injected) == 2
        agents = sorted(c.agent.id for c in injected)
        assert agents == ["bot_a", "bot_b"]

    def test_yaml_typo_in_default_fails_fast(self, tmp_path: Path) -> None:
        p = _write_yaml(
            tmp_path,
            "tool_policy:\n  default: deni\nagents: {}\n",
        )
        with pytest.raises(ConfigError, match="tool_policy.default"):
            load_config(p)
