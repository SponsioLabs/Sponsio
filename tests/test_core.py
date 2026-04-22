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
        import sponsio

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
