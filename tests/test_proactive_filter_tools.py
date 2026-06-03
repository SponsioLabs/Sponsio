"""Tests for ``BaseGuard._proactive_filter_tools`` + per-adapter
wrap-time filtering.

The helper is the v0.2 ``enforcement: proactive`` primitive that the
five wrap-based adapters (LangGraph, CrewAI, Agents SDK, Google ADK,
Vercel AI for completeness) call from their ``wrap()`` method to drop
denied tools before the agent's tool surface is ever built.

The contract pinned down here:

* When ``tool_policy.enforcement`` is the default ``reactive``, the
  helper is a no-op. every tool passes through, denied calls get
  caught reactively by ``guard_before`` at call time.
* When ``enforcement: proactive`` with ``default: deny``, the helper
  drops every tool whose name is not in ``approved``. Order is
  preserved so adapter UIs / tool pickers see the canonical layout.
* Temporal contracts (``must_precede``, ``count_at_most``, …) are NOT
  consulted at wrap time. The helper only filters on the static
  ``tool_policy.approved`` list. Otherwise a ``must_precede(A, B)``
  rule would permanently filter B out (assumption A hasn't fired on
  the empty wrap-time trace), which is the opposite of what the rule
  encodes. the user wants B available *after* A fires, not banned
  forever.
* ``allow`` (the legacy default) is a no-op regardless of
  ``enforcement``. there's no approved list to filter against.
* Per-adapter ``wrap()`` actually invokes the helper. Adapter tests
  are gated on the framework being installed.
"""

from __future__ import annotations

import pytest

from sponsio.config import ToolPolicySection
from sponsio.core import Sponsio


def _names(tools: list) -> list[str]:
    return [getattr(t, "name", None) or t.__name__ for t in tools]


class _NamedTool:
    """Tool stand-in: exposes ``.name`` like a LangChain Tool."""

    def __init__(self, name: str) -> None:
        self.name = name


class TestProactiveFilterToolsHelper:
    def test_reactive_is_noop(self) -> None:
        """Default enforcement (reactive) leaves the list untouched
        even when default-deny is set. wrap-time filtering is the
        opt-in behaviour gated on ``proactive``."""
        guard = Sponsio(
            agent_id="bot",
            tool_policy={
                "default": "deny",
                "approved": ["search"],
                "enforcement": "reactive",
            },
            verbose=False,
        )
        tools = [_NamedTool("search"), _NamedTool("delete_db")]
        kept = guard._proactive_filter_tools(tools, name_fn=lambda t: t.name)
        assert _names(kept) == ["search", "delete_db"]

    def test_proactive_with_deny_strips_unapproved(self) -> None:
        guard = Sponsio(
            agent_id="bot",
            tool_policy={
                "default": "deny",
                "approved": ["search", "read_file"],
                "enforcement": "proactive",
            },
            verbose=False,
        )
        tools = [
            _NamedTool("search"),
            _NamedTool("rm_rf"),
            _NamedTool("read_file"),
            _NamedTool("delete_db"),
        ]
        kept = guard._proactive_filter_tools(tools, name_fn=lambda t: t.name)
        assert _names(kept) == ["search", "read_file"]

    def test_proactive_with_allow_is_noop(self) -> None:
        """``proactive`` is meaningless without a deny posture. there's
        no approved list to gate against. Helper passes everything
        through."""
        guard = Sponsio(
            agent_id="bot",
            tool_policy={"default": "allow", "enforcement": "proactive"},
            verbose=False,
        )
        tools = [_NamedTool("search"), _NamedTool("delete_db")]
        kept = guard._proactive_filter_tools(tools, name_fn=lambda t: t.name)
        assert _names(kept) == ["search", "delete_db"]

    def test_preserves_input_order(self) -> None:
        guard = Sponsio(
            agent_id="bot",
            tool_policy={
                "default": "deny",
                "approved": ["c", "a", "b"],
                "enforcement": "proactive",
            },
            verbose=False,
        )
        tools = [_NamedTool("a"), _NamedTool("b"), _NamedTool("c")]
        kept = guard._proactive_filter_tools(tools, name_fn=lambda t: t.name)
        # Output mirrors input order, NOT approved-list order.
        assert _names(kept) == ["a", "b", "c"]

    def test_no_policy_is_noop(self) -> None:
        """Guard without any tool_policy kwarg defaults to allow + no
        approved list. Helper passes tools through unchanged."""
        guard = Sponsio(agent_id="bot", verbose=False)
        tools = [_NamedTool("anything")]
        kept = guard._proactive_filter_tools(tools, name_fn=lambda t: t.name)
        assert _names(kept) == ["anything"]

    def test_temporal_contract_does_not_affect_wrap_filter(self) -> None:
        """Critical invariant: a ``must_precede(A, B)`` contract would
        block B's call right now (assumption A hasn't fired) but must
        NOT cause B to drop from the wrap-time menu. Otherwise B would
        be permanently invisible to the agent, defeating the rule.
        Wrap-time filter only consults ``tool_policy.approved``."""
        guard = Sponsio(
            agent_id="bot",
            contracts=["must call `check_policy` before `issue_refund`"],
            tool_policy={
                "default": "deny",
                "approved": ["check_policy", "issue_refund"],
                "enforcement": "proactive",
            },
            verbose=False,
        )
        tools = [_NamedTool("check_policy"), _NamedTool("issue_refund")]
        kept = guard._proactive_filter_tools(tools, name_fn=lambda t: t.name)
        # Both pass. the temporal rule will reactively block
        # ``issue_refund`` at *call* time until ``check_policy`` fires,
        # but neither tool disappears from the menu.
        assert _names(kept) == ["check_policy", "issue_refund"]


class TestToolPolicyFromYaml:
    def test_yaml_enforcement_reaches_wrap_helper(self, tmp_path) -> None:
        """The ``enforcement:`` knob in a YAML config has to flow all
        the way to the adapter's wrap(). losing it on the way would
        silently degrade to reactive even when the user asked for
        proactive."""
        cfg = tmp_path / "sponsio.yaml"
        cfg.write_text(
            "tool_policy:\n"
            "  default: deny\n"
            "  approved: [search]\n"
            "  enforcement: proactive\n"
            "agents:\n"
            "  bot:\n"
            "    contracts: []\n"
        )
        guard = Sponsio(config=str(cfg), agent_id="bot", verbose=False)
        assert guard._tool_policy.enforcement == "proactive"
        tools = [_NamedTool("search"), _NamedTool("rm_rf")]
        assert _names(
            guard._proactive_filter_tools(tools, name_fn=lambda t: t.name)
        ) == ["search"]


class TestToolPolicyKwargAcceptedShapes:
    def test_accepts_section_instance(self) -> None:
        guard = Sponsio(
            agent_id="bot",
            tool_policy=ToolPolicySection(
                default="deny", approved=["a"], enforcement="proactive"
            ),
            verbose=False,
        )
        kept = guard._proactive_filter_tools(
            [_NamedTool("a"), _NamedTool("b")], name_fn=lambda t: t.name
        )
        assert _names(kept) == ["a"]

    def test_rejects_wrong_type_at_guard_level(self) -> None:
        with pytest.raises(TypeError, match="tool_policy"):
            # BaseGuard surface, not the Sponsio() factory. the
            # factory path was already covered by
            # ``test_tool_policy_inline.py``; this hits the underlying
            # guard.
            from sponsio.integrations.base import BaseGuard

            BaseGuard(agent_id="bot", tool_policy=42, verbose=False)


# ---------------------------------------------------------------------------
# Per-adapter wrap() tests. gated on each framework being installed.
# Each test confirms that ``wrap(tools)`` actually drops denied tools at
# bind time, not just at call time.
# ---------------------------------------------------------------------------


class TestLangGraphWrapFilters:
    def test_wrap_drops_denied_tools(self) -> None:
        pytest.importorskip("langgraph")
        pytest.importorskip("langchain_core")
        from langchain_core.tools import tool as lc_tool

        from sponsio.integrations.langgraph import LangGraphGuard

        @lc_tool
        def search(q: str) -> str:
            """search"""
            return q

        @lc_tool
        def delete_db(name: str) -> str:
            """delete"""
            return name

        guard = LangGraphGuard(
            agent_id="bot",
            tool_policy={
                "default": "deny",
                "approved": ["search"],
                "enforcement": "proactive",
            },
            verbose=False,
        )
        node = guard.wrap([search, delete_db])
        # ``ToolNode.tools_by_name`` is the canonical map of bound
        # tools; the denied one must be absent.
        bound = getattr(node, "tools_by_name", None) or {
            t.name: t for t in getattr(node, "tools", [])
        }
        assert "search" in bound
        assert "delete_db" not in bound


class TestCrewAIWrapFilters:
    def test_wrap_drops_denied_tools(self) -> None:
        pytest.importorskip("crewai")
        from sponsio.integrations.crewai import CrewAIGuard

        def search(q: str) -> str:
            """Search for q."""
            return q

        def delete_db(name: str) -> str:
            """Delete the named database."""
            return name

        guard = CrewAIGuard(
            agent_id="bot",
            tool_policy={
                "default": "deny",
                "approved": ["search"],
                "enforcement": "proactive",
            },
            verbose=False,
        )
        wrapped = guard.wrap([search, delete_db])
        bound_names = {getattr(t, "name", getattr(t, "__name__", "")) for t in wrapped}
        assert "search" in bound_names
        assert "delete_db" not in bound_names


class TestAgentsSDKWrapFilters:
    def test_wrap_drops_denied_tools(self) -> None:
        pytest.importorskip("agents")
        from sponsio.integrations.agents import AgentsSDKGuard

        def search(q: str) -> str:
            return q

        def delete_db(name: str) -> str:
            return name

        guard = AgentsSDKGuard(
            agent_id="bot",
            tool_policy={
                "default": "deny",
                "approved": ["search"],
                "enforcement": "proactive",
            },
            verbose=False,
        )
        wrapped = guard.wrap([search, delete_db])
        bound_names = {getattr(t, "name", getattr(t, "__name__", "")) for t in wrapped}
        assert "search" in bound_names
        assert "delete_db" not in bound_names


class TestGoogleADKWrapFilters:
    def test_wrap_drops_denied_tools(self) -> None:
        pytest.importorskip("google.adk")
        from sponsio.integrations.google_adk import GoogleADKGuard

        def search(q: str) -> str:
            return q

        def delete_db(name: str) -> str:
            return name

        guard = GoogleADKGuard(
            agent_id="bot",
            tool_policy={
                "default": "deny",
                "approved": ["search"],
                "enforcement": "proactive",
            },
            verbose=False,
        )
        wrapped = guard.wrap([search, delete_db])
        bound_names = {getattr(t, "__name__", "") for t in wrapped}
        assert "search" in bound_names
        assert "delete_db" not in bound_names
