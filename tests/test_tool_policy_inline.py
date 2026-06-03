"""Tests for the inline ``tool_policy=`` kwarg on ``Sponsio()``.

Users who construct a guard programmatically (without a yaml file) need
the same default-deny posture available in YAML. The kwarg accepts the
same dict shape as the YAML section so docs and examples can share one
mental model.

Locked-in behaviour:

* ``tool_policy={"default": "deny", "approved": [...]}`` prepends a
  synthesized ``tool_allowlist`` contract to ``contracts``.
* The synthesized contract goes *before* user contracts so the deny
  rule fires first ("first-line defence").
* ``default: allow`` (or omitting the kwarg) is a no-op. no contract
  injected.
* A ``ToolPolicySection`` instance is accepted in place of a dict for
  callers who already parsed one.
* ``config=`` + ``tool_policy=`` is rejected. yaml owns policy when
  both are present; users should set it in the yaml instead.
* Invalid shapes (typos in ``default``, wrong types) raise at
  construction so a misconfig never ships silently.
"""

from __future__ import annotations

import pytest

from sponsio.config import ToolPolicySection
from sponsio.core import Sponsio


class TestInlineToolPolicy:
    def test_deny_injects_first_contract(self) -> None:
        guard = Sponsio(
            agent_id="bot",
            contracts=["tool `execute` at most 5 times"],
            tool_policy={"default": "deny", "approved": ["search"]},
            verbose=False,
        )
        contracts = guard._monitor._system.contracts
        assert len(contracts) == 2
        first = contracts[0].guarantee
        assert getattr(first, "pattern_name", None) == "tool_allowlist"
        assert "default-deny" in (contracts[0].desc or "")

    def test_deny_with_no_user_contracts(self) -> None:
        """A common shape: user just locks down tool access without
        any other rules. Synthesized contract is the only one."""
        guard = Sponsio(
            agent_id="bot",
            tool_policy={"default": "deny", "approved": ["search"]},
            verbose=False,
        )
        contracts = guard._monitor._system.contracts
        assert len(contracts) == 1
        assert contracts[0].guarantee.pattern_name == "tool_allowlist"

    def test_allow_is_noop(self) -> None:
        guard = Sponsio(
            agent_id="bot",
            contracts=["tool `execute` at most 5 times"],
            tool_policy={"default": "allow"},
            verbose=False,
        )
        contracts = guard._monitor._system.contracts
        # Only the user's own contract. no tool_allowlist prefix.
        assert len(contracts) == 1
        assert contracts[0].guarantee.pattern_name != "tool_allowlist"

    def test_omitted_is_noop(self) -> None:
        guard = Sponsio(
            agent_id="bot",
            contracts=["tool `execute` at most 5 times"],
            verbose=False,
        )
        assert len(guard._monitor._system.contracts) == 1

    def test_accepts_tool_policy_section_instance(self) -> None:
        policy = ToolPolicySection(default="deny", approved=["search"])
        guard = Sponsio(
            agent_id="bot",
            tool_policy=policy,
            verbose=False,
        )
        contracts = guard._monitor._system.contracts
        assert len(contracts) == 1
        assert contracts[0].guarantee.pattern_name == "tool_allowlist"

    def test_rejects_wrong_type(self) -> None:
        with pytest.raises(TypeError, match="tool_policy"):
            Sponsio(
                agent_id="bot",
                tool_policy="deny",  # type: ignore[arg-type]
                verbose=False,
            )

    def test_propagates_validation_errors(self) -> None:
        from sponsio.config import ConfigError

        with pytest.raises(ConfigError, match="tool_policy.default"):
            Sponsio(
                agent_id="bot",
                tool_policy={"default": "deni"},
                verbose=False,
            )

    def test_config_and_tool_policy_are_mutually_exclusive(self, tmp_path) -> None:
        cfg = tmp_path / "sponsio.yaml"
        cfg.write_text("agents:\n  bot:\n    contracts: []\n")
        with pytest.raises(
            ValueError, match="Cannot combine 'config' with 'tool_policy'"
        ):
            Sponsio(
                agent_id="bot",
                config=str(cfg),
                tool_policy={"default": "deny"},
                verbose=False,
            )
