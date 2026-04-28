"""Tests for sponsio.core and top-level imports."""

from __future__ import annotations

import pytest


class TestInit:
    def test_init_no_framework(self):
        import sponsio
        from sponsio.integrations.base import BaseGuard

        guard = sponsio.Sponsio(
            agent_id="bot",
            contracts=["tool `X` at most 3 times"],
            verbose=False,
        )
        assert type(guard) is BaseGuard
        assert guard.agent_id == "bot"

    def test_init_langgraph_framework(self):
        import sponsio

        guard = sponsio.Sponsio(
            framework="langgraph",
            agent_id="bot",
            contracts=["tool `X` at most 3 times"],
            verbose=False,
        )
        assert type(guard).__name__ == "LangGraphGuard"
        assert hasattr(guard, "tool_node")

    def test_init_openai_framework(self):
        import sponsio

        guard = sponsio.Sponsio(
            framework="openai",
            agent_id="bot",
            contracts=["tool `X` at most 3 times"],
            verbose=False,
        )
        assert type(guard).__name__ == "OpenAIGuard"

    def test_init_google_adk_framework(self):
        import sponsio

        guard = sponsio.Sponsio(
            framework="google-adk",
            agent_id="bot",
            contracts=["tool `X` at most 3 times"],
            verbose=False,
        )
        assert type(guard).__name__ == "GoogleADKGuard"

    def test_framework_namespace_init(self):
        from sponsio.langgraph import Sponsio as langgraph_Sponsio
        from sponsio.openai import Sponsio as openai_Sponsio

        langgraph_guard = langgraph_Sponsio(
            agent_id="bot",
            contracts=["tool `X` at most 3 times"],
            verbose=False,
        )
        openai_guard = openai_Sponsio(
            agent_id="bot",
            contracts=["tool `X` at most 3 times"],
            verbose=False,
        )

        assert type(langgraph_guard).__name__ == "LangGraphGuard"
        assert type(openai_guard).__name__ == "OpenAIGuard"

    def test_init_bad_framework(self):
        import sponsio

        with pytest.raises(ValueError, match="Unknown framework"):
            sponsio.Sponsio(framework="flask", contracts=["x"])

    def test_init_with_contract_dict(self):
        """The canonical per-contract API: one dict = one A/E pair."""
        import sponsio

        guard = sponsio.Sponsio(
            agent_id="bot",
            contracts=[
                {
                    "assumption": "tool `A` must precede `B`",
                    "enforcement": "tool `B` at most 2 times",
                }
            ],
            verbose=False,
        )
        contract = guard._system.contracts[0]
        assert len(contract.assumptions) == 1
        assert len(contract.enforcements) == 1

    def test_init_with_contract_builder(self):
        """Fluent Python contracts map to the same A/E model."""
        import sponsio

        guard = sponsio.Sponsio(
            agent_id="bot",
            contracts=[
                sponsio.contract("refund gate")
                .assume("tool `A` must precede `B`")
                .enforce("tool `B` at most 2 times")
            ],
            verbose=False,
        )
        contract = guard._system.contracts[0]
        assert contract.desc == "refund gate"
        assert len(contract.assumptions) == 1
        assert len(contract.enforcements) == 1

    def test_contract_builder_threshold_alias(self):
        import sponsio

        guard = sponsio.Sponsio(
            agent_id="bot",
            contracts=[
                sponsio.contract("scored rule")
                .enforce("tool `B` at most 2 times")
                .threshold(beta=0.8)
            ],
            verbose=False,
        )
        contract = guard._system.contracts[0]
        assert contract.alpha == 1.0
        assert contract.beta == 0.8

    def test_contract_builder_requires_enforcement(self):
        import sponsio

        with pytest.raises(ValueError, match=r"enforce"):
            sponsio.Sponsio(
                agent_id="bot",
                contracts=[sponsio.contract("missing E").assume("called `A`")],
                verbose=False,
            )

    def test_init_multiple_independent_contracts(self):
        """Multiple contracts must be independent — A1 does not gate E2."""
        import sponsio

        guard = sponsio.Sponsio(
            agent_id="bot",
            contracts=[
                {
                    "assumption": "tool `A` must precede `B`",
                    "enforcement": "tool `B` at most 2 times",
                },
                {"enforcement": "tool `X` at most 5 times"},
            ],
            verbose=False,
        )
        contracts = guard._system.contracts
        assert len(contracts) == 2
        assert contracts[0].assumption is not None
        assert contracts[1].assumption is None

    def test_init_python_rejects_short_keys(self):
        """Python contract dicts must use full names; A/E is YAML-only."""
        import sponsio

        with pytest.raises(ValueError, match="YAML-only"):
            sponsio.Sponsio(
                agent_id="bot",
                contracts=[
                    {
                        "A": "tool `A` must precede `B`",
                        "E": "tool `B` at most 2 times",
                    }
                ],
                verbose=False,
            )

    def test_init_list_valued_and(self):
        """List-valued assumption / enforcement is preserved for AND-combine."""
        import sponsio

        guard = sponsio.Sponsio(
            agent_id="bot",
            contracts=[
                {
                    "enforcement": [
                        "tool `X` at most 3 times",
                        "tool `Y` at most 2 times",
                    ]
                }
            ],
            verbose=False,
        )
        contract = guard._system.contracts[0]
        assert len(contract.enforcements) == 2

    def test_init_list_valued_fields_parse_once(self):
        """List fields should not double-register parsed constraints."""
        import sponsio

        class Store:
            def __init__(self):
                self.imported = None

            def import_user_defined(self, formulas):
                self.imported = list(formulas)

        store = Store()
        sponsio.Sponsio(
            agent_id="bot",
            contracts=[
                {
                    "enforcement": [
                        "tool `X` at most 3 times",
                        "tool `Y` at most 2 times",
                    ]
                }
            ],
            store=store,
            verbose=False,
        )
        assert store.imported is not None
        assert len(store.imported) == 2

    def test_init_config_file(self, tmp_path):
        import sponsio

        config = tmp_path / "sponsio.yaml"
        config.write_text(
            'agents:\n  bot:\n    contracts:\n      - E: "tool `X` at most 3 times"\n'
        )
        guard = sponsio.Sponsio(config=str(config), agent_id="bot", verbose=False)
        assert guard.agent_id == "bot"

    def test_init_config_with_framework(self, tmp_path):
        import sponsio

        config = tmp_path / "sponsio.yaml"
        config.write_text(
            'agents:\n  bot:\n    contracts:\n      - E: "tool `X` at most 3 times"\n'
        )
        guard = sponsio.Sponsio(
            framework="langgraph",
            config=str(config),
            agent_id="bot",
            verbose=False,
        )
        assert type(guard).__name__ == "LangGraphGuard"

    def test_init_config_and_inline_raises(self):
        import sponsio

        with pytest.raises(ValueError, match="Cannot combine"):
            sponsio.Sponsio(config="some.yaml", contracts=["x"])

    def test_init_verbose_false(self):
        import sponsio

        guard = sponsio.Sponsio(
            agent_id="bot",
            contracts=["tool `X` at most 3 times"],
            verbose=False,
        )
        assert guard._verbose is False

    def test_init_dashboard_string(self):
        import warnings

        import sponsio

        # localhost dashboards emit a one-shot SSRF/local-network
        # warning by design — operators see it when they wire the dev
        # dashboard. Suppressed here so the test output stays clean.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            guard = sponsio.Sponsio(
                agent_id="bot",
                contracts=["tool `X` at most 3 times"],
                dashboard="http://localhost:9999",
                verbose=False,
            )
        assert guard._dashboard_url == "http://localhost:9999"

    def test_init_dashboard_false(self):
        import sponsio

        guard = sponsio.Sponsio(
            agent_id="bot",
            contracts=["tool `X` at most 3 times"],
            dashboard=False,
            verbose=False,
        )
        assert guard._dashboard_url is None

    def test_init_framework_case_insensitive(self):
        import sponsio

        guard = sponsio.Sponsio(
            framework="LangGraph",
            agent_id="bot",
            contracts=["tool `X` at most 3 times"],
            verbose=False,
        )
        assert type(guard).__name__ == "LangGraphGuard"


class TestPerContractSemantics:
    """Regression tests: one contract's assumption must NOT gate another's enforcement."""

    def test_failed_assumption_on_one_contract_does_not_skip_other(self):
        import sponsio

        guard = sponsio.Sponsio(
            agent_id="bot",
            contracts=[
                # Contract 1: assumption A will fail (A is never called).
                {
                    "assumption": "tool `never_called` must precede `dummy`",
                    "enforcement": "tool `whatever` at most 1 times",
                },
                # Contract 2: unconditional — must still catch violation.
                {"enforcement": "tool `banned` at most 0 times"},
            ],
            verbose=False,
        )

        result = guard.guard_before("banned")
        # Contract 2's unconditional enforcement fires regardless of C1's A.
        assert result.blocked, "Unconditional contract 2 should block 'banned'"

    def test_assumption_holds_then_enforcement_checked(self):
        import sponsio

        guard = sponsio.Sponsio(
            agent_id="bot",
            contracts=[
                {
                    "assumption": "tool `banned` at most 3 times",
                    "enforcement": "tool `banned` at most 0 times",
                }
            ],
            verbose=False,
        )

        result = guard.guard_before("banned")
        assert result.blocked


class TestTopLevelImports:
    def test_import_Sponsio(self):
        from sponsio import Sponsio

        assert callable(Sponsio)

    def test_import_version(self):
        from sponsio import __version__

        assert __version__.startswith("0.")

    def test_import_load_config(self):
        from sponsio import load_config

        assert callable(load_config)

    def test_import_models(self):
        from sponsio import Agent

        assert Agent is not None

    def test_import_langgraph_guard(self):
        from sponsio import LangGraphGuard

        assert LangGraphGuard is not None

    def test_import_backward_compat(self):
        from sponsio import ContractGuard, LangGraphGuard

        assert ContractGuard is LangGraphGuard

    def test_import_agents_backward_compat(self):
        from sponsio import AgentsGuard, AgentsSDKGuard

        assert AgentsGuard is AgentsSDKGuard

    def test_import_google_adk_guard(self):
        from sponsio import GoogleADKGuard

        assert GoogleADKGuard is not None

    def test_import_patch_openai(self):
        from sponsio import patch_openai, unpatch_openai

        assert callable(patch_openai)
        assert callable(unpatch_openai)

    def test_internal_imports_still_work(self):
        from sponsio.runtime.evaluators import DetEvaluator
        from sponsio.runtime.monitor import RuntimeMonitor

        assert RuntimeMonitor is not None
        assert DetEvaluator is not None

    def test_bad_import_raises(self):
        with pytest.raises((AttributeError, ImportError)):
            from sponsio import NonExistentThing  # noqa: F401


class TestAgentIdFallback:
    """Behaviour of `Sponsio(config=...)` when the requested agent_id
    isn't a literal match for the YAML's `agents:` keys.

    Goal of the fallback: keep users out of "edit four files in sync
    just to change a name" hell.  In single-agent configs there's
    only one possible answer; in multi-agent configs we can't guess.
    """

    @pytest.fixture
    def single_agent_yaml(self, tmp_path):
        path = tmp_path / "sponsio.yaml"
        path.write_text(
            """
version: "1"
defaults:
  mode: enforce
agents:
  sre_optimizer:
    contracts:
      - E:
          pattern: rate_limit
          args: [delete_snapshot, 5]
"""
        )
        return path

    @pytest.fixture
    def multi_agent_yaml(self, tmp_path):
        path = tmp_path / "sponsio.yaml"
        path.write_text(
            """
version: "1"
defaults:
  mode: enforce
agents:
  alice:
    contracts:
      - E:
          pattern: rate_limit
          args: [foo, 1]
  bob:
    contracts:
      - E:
          pattern: rate_limit
          args: [bar, 1]
"""
        )
        return path

    def test_default_agent_id_picks_only_agent_silently(self, single_agent_yaml):
        # Pre-fix behaviour preserved: default agent_id="agent" with
        # a single-agent config silently auto-infers, no warning.
        import sponsio

        guard = sponsio.Sponsio(config=str(single_agent_yaml))
        assert guard.agent_id == "sre_optimizer"

    def test_explicit_matching_agent_id_works(self, single_agent_yaml):
        import sponsio

        guard = sponsio.Sponsio(config=str(single_agent_yaml), agent_id="sre_optimizer")
        assert guard.agent_id == "sre_optimizer"

    def test_explicit_mismatched_agent_id_falls_back_with_warning(
        self, single_agent_yaml
    ):
        # The case the user actually hit: yaml has `sre_optimizer`,
        # caller passes `agent_id="something_else"`.  Old behaviour:
        # ValueError "Agent not found".  New: fall back to the only
        # agent, surface a UserWarning so it's audible.
        import sponsio

        with pytest.warns(UserWarning, match="not found in config"):
            guard = sponsio.Sponsio(
                config=str(single_agent_yaml), agent_id="totally_wrong"
            )
        assert guard.agent_id == "sre_optimizer"

    def test_multi_agent_mismatch_still_errors(self, multi_agent_yaml):
        # Multi-agent: there's no unambiguous fallback, raise so the
        # user fixes the call site.  Error message lists the
        # available agents so they don't have to grep the yaml.
        import sponsio

        with pytest.raises(ValueError, match=r"multiple agents.*alice.*bob"):
            sponsio.Sponsio(config=str(multi_agent_yaml), agent_id="not_alice_or_bob")

    def test_multi_agent_explicit_match_works(self, multi_agent_yaml):
        import sponsio

        guard = sponsio.Sponsio(config=str(multi_agent_yaml), agent_id="alice")
        assert guard.agent_id == "alice"
