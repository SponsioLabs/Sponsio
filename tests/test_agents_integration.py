"""Unit tests for sponsio/integrations/agents.py — OpenAI Agents SDK integration."""

from __future__ import annotations

import builtins
import sys
import types as _types

import pytest

from sponsio.integrations.agents import (
    AgentsSDKGuard,
    ToolCallBlocked,
    _call_args,
    _decode_json_args,
    _extract_function,
    _function_tool_name_kw,
    _is_function_tool,
)


# ---------------------------------------------------------------------------
# Test AgentsSDKGuard core logic (without openai-agents dependency)
# ---------------------------------------------------------------------------


class TestCheckToolCall:
    def test_allowed(self):
        guard = AgentsSDKGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        result = guard.check_tool_call("check_policy")
        assert result.blocked is False

    def test_blocked(self):
        guard = AgentsSDKGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        result = guard.check_tool_call("issue_refund")
        assert result.blocked is True

    def test_correct_order(self):
        guard = AgentsSDKGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        guard.check_tool_call("check_policy")
        result = guard.check_tool_call("issue_refund")
        assert result.blocked is False

    def test_mutual_exclusion(self):
        guard = AgentsSDKGuard(
            contracts=["tools `approve` and `reject` are mutually exclusive"]
        )
        guard.check_tool_call("approve")
        result = guard.check_tool_call("reject")
        assert result.blocked is True

    def test_unrelated_tool_allowed(self):
        guard = AgentsSDKGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        result = guard.check_tool_call("lookup_customer")
        assert result.blocked is False

    def test_last_check_updated(self):
        guard = AgentsSDKGuard(contracts=["tool `A` must precede `B`"])
        guard.check_tool_call("A")
        assert guard.last_check is not None
        assert guard.last_check.blocked is False

    def test_violations_recorded(self):
        guard = AgentsSDKGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        guard.check_tool_call("issue_refund")
        assert len(guard.violations) > 0

    def test_no_contracts_allows_everything(self):
        guard = AgentsSDKGuard()
        result = guard.check_tool_call("anything")
        assert result.blocked is False


# ---------------------------------------------------------------------------
# Test _extract_function
# ---------------------------------------------------------------------------


class TestExtractFunction:
    def test_plain_callable(self):
        def my_fn():
            pass

        assert _extract_function(my_fn) is my_fn

    def test_object_with_fn_attr(self):
        class MockTool:
            def fn(self):
                pass

        tool = MockTool()
        assert _extract_function(tool) == tool.fn

    def test_object_with_func_attr(self):
        class MockTool:
            def func(self):
                pass

        tool = MockTool()
        assert _extract_function(tool) == tool.func

    def test_non_callable_raises(self):
        with pytest.raises(TypeError):
            _extract_function(42)  # type: ignore


# ---------------------------------------------------------------------------
# Test ToolCallBlocked exception
# ---------------------------------------------------------------------------


class TestToolCallBlocked:
    def test_exception_attrs(self):
        exc = ToolCallBlocked("issue_refund", "must_precede", "blocked message")
        assert exc.tool_name == "issue_refund"
        assert exc.constraint == "must_precede"
        assert "blocked message" in str(exc)


# ---------------------------------------------------------------------------
# Test wrap_tool (requires openai-agents)
# ---------------------------------------------------------------------------


class TestWrapTool:
    def test_wrap_tool_requires_agents(self, monkeypatch):
        # Force the import inside ``wrap_tool`` to fail regardless of
        # whether ``openai-agents`` is installed in the test env.
        # We patch ``builtins.__import__`` so the relative-from import
        # (``from agents import function_tool``) raises just like in a
        # bare environment, without disturbing other imports the test
        # suite needs.
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "agents":
                raise ImportError("No module named 'agents'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        # Drop any cached module entry too so a previously-imported
        # ``agents`` package can't shadow the simulated ImportError.
        monkeypatch.setitem(sys.modules, "agents", None)

        guard = AgentsSDKGuard(contracts=["tool `A` must precede `B`"])

        def dummy_tool():
            return "ok"

        with pytest.raises(ImportError, match="openai-agents is required"):
            guard.wrap_tool(dummy_tool)


class TestFunctionToolNameKw:
    """`_function_tool_name_kw` picks the kwarg name accepted by the
    installed Agents SDK.  Pre-rename SDKs took ``name=``;
    post-rename SDKs take ``name_override=``.  We pin both branches
    so a future SDK swap can't silently regress."""

    def test_post_rename_sdk(self):
        def fake(name_override=None):  # mimic new SDK signature
            return name_override

        assert _function_tool_name_kw(fake) == "name_override"

    def test_pre_rename_sdk(self):
        def fake(name=None):  # mimic old SDK signature
            return name

        assert _function_tool_name_kw(fake) == "name"

    def test_unintrospectable_falls_back_to_override(self):
        # C-implemented callables sometimes refuse signature
        # inspection; we default to the post-rename spelling since
        # that's what the README documents.
        assert _function_tool_name_kw(len) == "name_override"


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------


class TestStateManagement:
    def test_reset_clears_state(self):
        guard = AgentsSDKGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        guard.check_tool_call("issue_refund")
        assert len(guard.violations) > 0

        guard.reset()
        assert len(guard.violations) == 0

    def test_summary(self):
        guard = AgentsSDKGuard(
            contracts=["tool `check_policy` must precede `issue_refund`"]
        )
        assert "No violations" in guard.summary()

        guard.check_tool_call("issue_refund")
        assert "violation" in guard.summary().lower()


# ---------------------------------------------------------------------------
# Argument visibility: the SDK passes parameters positionally
# ---------------------------------------------------------------------------


def _install_fake_agents_sdk(monkeypatch):
    """A ``function_tool`` that hands the parsed JSON to the function the
    way the real SDK does: positionally, in signature order."""
    import inspect as _inspect
    import json as _json

    class FakeFunctionTool:
        def __init__(self, fn, name):
            self.name = name
            self.params_json_schema = {"type": "object"}
            self._fn = fn
            sig = _inspect.signature(fn)

            async def on_invoke_tool(ctx, input_json: str):
                data = _json.loads(input_json or "{}")
                positional = [data.get(p) for p in sig.parameters]
                try:
                    result = fn(*positional)
                    if _inspect.isawaitable(result):
                        result = await result
                except Exception as exc:  # the SDK's default failure handler
                    return (
                        "An error occurred while running the tool. Please try "
                        f"again. Error: {exc}"
                    )
                return result

            self.on_invoke_tool = on_invoke_tool

    def function_tool(fn=None, *, name_override=None):
        def deco(f):
            return FakeFunctionTool(f, name_override or f.__name__)

        return deco(fn) if fn is not None else deco

    mod = _types.ModuleType("agents")
    mod.function_tool = function_tool  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agents", mod)
    return mod


class TestPositionalArguments:
    def test_call_args_binds_positionals_to_parameter_names(self):
        def issue_refund(order_id: str, amount: int) -> str:
            return "ok"

        assert _call_args(issue_refund, ("o-1", 5), {}) == {
            "order_id": "o-1",
            "amount": 5,
        }
        assert _call_args(issue_refund, ("o-1",), {"amount": 5}) == {
            "order_id": "o-1",
            "amount": 5,
        }

    def test_call_args_drops_the_sdk_context_object(self):
        class RunContextWrapper:  # the SDK passes this first for context tools
            context = None
            usage = None

        def tool(ctx, amount: int) -> str:
            return "ok"

        assert _call_args(tool, (RunContextWrapper(), 7), {}) == {"amount": 7}

    def test_decode_json_args_keeps_malformed_payload(self):
        assert _decode_json_args('{"a": 1}') == {"a": 1}
        assert _decode_json_args("") == {}
        bad = _decode_json_args("{not json")
        assert bad["_sponsio_unparseable"] is True
        assert bad["_raw_arguments"] == "{not json"

    def test_plain_callable_argument_contract_fires_via_sdk(self, monkeypatch):
        """Regression: the guard used to receive ``{}`` because only
        ``kwargs`` were forwarded, so every argument contract passed."""
        import asyncio

        import sponsio
        from sponsio.patterns.library import arg_blacklist

        _install_fake_agents_sdk(monkeypatch)

        ran: list[int] = []

        def issue_refund(amount: int) -> str:
            ran.append(amount)
            return f"refunded {amount}"

        guard = AgentsSDKGuard(
            agent_id="bot",
            contracts=[
                sponsio.contract("no five-digit refunds").guarantees(
                    arg_blacklist("issue_refund", "amount", [r"^\d{5,}$"])
                )
            ],
        )
        wrapped = guard.wrap([issue_refund])[0]
        out = asyncio.run(wrapped.on_invoke_tool(None, '{"amount": 99999}'))
        assert "BLOCKED by contract" in str(out) or "error" in str(out).lower()
        assert ran == []
        assert guard.last_check is not None and guard.last_check.blocked

        out = asyncio.run(wrapped.on_invoke_tool(None, '{"amount": 42}'))
        assert out == "refunded 42"
        assert ran == [42]


class TestFunctionToolObjects:
    def test_is_function_tool_recognises_the_sdk_shape(self):
        class FT:
            params_json_schema = {}

            async def on_invoke_tool(self, ctx, input_json):
                return "x"

        assert _is_function_tool(FT())
        assert not _is_function_tool(lambda: None)

    def test_decorated_tool_is_wrapped_at_on_invoke_tool(self, monkeypatch):
        """A ``FunctionTool`` keeps its name/schema and is gated on the JSON
        the SDK hands to ``on_invoke_tool``. It used to crash at wrap time."""
        import asyncio

        mod = _install_fake_agents_sdk(monkeypatch)

        body_ran: list[str] = []

        def issue_refund(order_id: str) -> str:
            body_ran.append(order_id)
            return f"refunded {order_id}"

        decorated = mod.function_tool(issue_refund)
        guard = AgentsSDKGuard(
            agent_id="bot",
            contracts=["tool `check_policy` must precede `issue_refund`"],
        )
        wrapped = guard.wrap([decorated])[0]
        assert wrapped.name == "issue_refund"
        assert wrapped.params_json_schema == decorated.params_json_schema

        out = asyncio.run(wrapped.on_invoke_tool(None, '{"order_id": "o-1"}'))
        assert str(out).startswith("BLOCKED by contract:")
        assert body_ran == []

        guard.check_tool_call("check_policy")
        out = asyncio.run(wrapped.on_invoke_tool(None, '{"order_id": "o-1"}'))
        assert out == "refunded o-1"
        assert body_ran == ["o-1"]
