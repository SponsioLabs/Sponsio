"""``wrap_graph`` / ``monitor_graph`` gate each node *before* it runs.

Regression tests for an external audit finding (Trenyx, 2026-09): the
whole-graph wrappers used to iterate the inner graph's stream and check
each node only after its update had been produced, then discard the
verdict. Every test here asserts on a side-effect log written by the
node bodies, so "blocked" means the body never ran, not merely that a
violation was recorded.
"""

from __future__ import annotations

import asyncio
from typing import TypedDict

import pytest

pytest.importorskip("langgraph")

from langgraph.graph import END, START, StateGraph  # noqa: E402

from sponsio.integrations.langgraph import (  # noqa: E402
    LangGraphGuard,
    ToolCallBlocked,
    monitor_graph,
)

ORDER = ["tool `parser` must precede `forecaster`"]


class _State(TypedDict, total=False):
    log: list


def _node(name: str, ran: list):
    def body(state):
        ran.append(name)
        return {"log": state.get("log", []) + [name]}

    return body


def _pipeline(order: list[str], ran: list):
    """A linear graph running ``order`` left to right."""
    b = StateGraph(_State)
    for name in order:
        b.add_node(name, _node(name, ran))
    b.add_edge(START, order[0])
    for left, right in zip(order, order[1:]):
        b.add_edge(left, right)
    b.add_edge(order[-1], END)
    return b.compile()


def _guard() -> LangGraphGuard:
    return LangGraphGuard(agent_id="pipe", contracts=ORDER, mode="enforce")


async def _drain(ait):
    return [c async for c in ait]


# ---------------------------------------------------------------------------
# Blocked: the offending node body must never execute
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entry",
    [
        "invoke",
        "stream",
        "ainvoke",
        "astream",
        "batch",
        "abatch",
    ],
)
def test_violating_node_is_stopped_before_it_runs(entry):
    ran: list = []
    wrapped = _guard().wrap_graph(_pipeline(["forecaster", "parser"], ran))
    state = {"log": []}

    calls = {
        "invoke": lambda: wrapped.invoke(state),
        "stream": lambda: list(wrapped.stream(state)),
        "ainvoke": lambda: asyncio.run(wrapped.ainvoke(state)),
        "astream": lambda: asyncio.run(_drain(wrapped.astream(state))),
        "batch": lambda: wrapped.batch([state]),
        "abatch": lambda: asyncio.run(wrapped.abatch([state])),
    }
    with pytest.raises(ToolCallBlocked) as excinfo:
        calls[entry]()

    assert excinfo.value.tool_name == "forecaster"
    assert "must precede" in str(excinfo.value)
    assert ran == [], f"node body ran before the verdict via {entry}: {ran}"


def test_block_mid_graph_keeps_earlier_nodes_and_stops_later_ones():
    """``forecaster`` is the second of three nodes; the first runs, the
    second is refused, and the third never starts."""
    ran: list = []
    wrapped = _guard().wrap_graph(_pipeline(["fetch", "forecaster", "parser"], ran))
    with pytest.raises(ToolCallBlocked):
        wrapped.invoke({"log": []})
    assert ran == ["fetch"]


def test_violation_is_recorded_on_the_guard():
    ran: list = []
    guard = _guard()
    wrapped = guard.wrap_graph(_pipeline(["forecaster", "parser"], ran))
    with pytest.raises(ToolCallBlocked):
        wrapped.invoke({"log": []})
    assert any(v["tool"] == "forecaster" for v in guard.violations)


# ---------------------------------------------------------------------------
# Allowed: the wrapper is transparent when contracts hold
# ---------------------------------------------------------------------------


def test_correct_order_runs_and_returns_final_state():
    ran: list = []
    wrapped = _guard().wrap_graph(_pipeline(["parser", "forecaster"], ran))
    out = wrapped.invoke({"log": []})
    assert out == {"log": ["parser", "forecaster"]}
    assert ran == ["parser", "forecaster"]


def test_correct_order_works_without_a_checkpointer():
    """The old ``invoke`` read the result back through ``get_state``,
    which raises on a graph compiled without a checkpointer."""
    ran: list = []
    wrapped = _guard().wrap_graph(_pipeline(["parser", "forecaster"], ran))
    assert wrapped.invoke({"log": []})["log"] == ["parser", "forecaster"]


def test_stream_yields_inner_chunks_when_allowed():
    ran: list = []
    wrapped = _guard().wrap_graph(_pipeline(["parser", "forecaster"], ran))
    chunks = list(wrapped.stream({"log": []}, stream_mode="updates"))
    assert [list(c) for c in chunks] == [["parser"], ["forecaster"]]


def test_observe_mode_records_but_does_not_stop(monkeypatch):
    # conftest pins SPONSIO_MODE=enforce for the suite, and the env var
    # outranks the explicit argument; drop it to exercise shadow mode.
    monkeypatch.delenv("SPONSIO_MODE", raising=False)
    ran: list = []
    guard = LangGraphGuard(agent_id="pipe", contracts=ORDER, mode="observe")
    wrapped = guard.wrap_graph(_pipeline(["forecaster", "parser"], ran))
    wrapped.invoke({"log": []})
    assert ran == ["forecaster", "parser"]
    assert any(v["tool"] == "forecaster" for v in guard.violations)


def test_block_false_records_but_does_not_stop():
    """``LangGraphGuard(block=False)`` opts out of raising, as it does
    for the guard's own ``on_tool_start`` callback."""
    ran: list = []
    guard = LangGraphGuard(
        agent_id="pipe", contracts=ORDER, mode="enforce", block=False
    )
    wrapped = guard.wrap_graph(_pipeline(["forecaster", "parser"], ran))
    wrapped.invoke({"log": []})
    assert ran == ["forecaster", "parser"]
    assert any(v["tool"] == "forecaster" for v in guard.violations)


def test_monitor_only_wrapper_runs_everything():
    ran: list = []
    wrapped = monitor_graph(_pipeline(["forecaster", "parser"], ran))
    assert wrapped.invoke({"log": []})["log"] == ["forecaster", "parser"]


def test_monitor_graph_with_contracts_enforces():
    ran: list = []
    wrapped = monitor_graph(
        _pipeline(["forecaster", "parser"], ran),
        contracts=ORDER,
        mode="enforce",
    )
    with pytest.raises(ToolCallBlocked):
        wrapped.invoke({"log": []})
    assert ran == []


# ---------------------------------------------------------------------------
# Plumbing the gate must not disturb
# ---------------------------------------------------------------------------


def test_user_callbacks_are_preserved():
    from langchain_core.callbacks.base import BaseCallbackHandler

    class Seen(BaseCallbackHandler):
        def __init__(self):
            self.names = []

        def on_chain_start(self, serialized, inputs, *, run_id, **kw):
            md = kw.get("metadata") or {}
            if kw.get("name") == md.get("langgraph_node"):
                self.names.append(kw["name"])

    seen = Seen()
    ran: list = []
    wrapped = _guard().wrap_graph(_pipeline(["parser", "forecaster"], ran))
    cfg = {"callbacks": [seen], "tags": ["mine"]}
    wrapped.invoke({"log": []}, config=cfg)
    assert seen.names == ["parser", "forecaster"]
    # the caller's config object is left alone
    assert cfg == {"callbacks": [seen], "tags": ["mine"]}


def test_subgraph_named_after_its_node_is_checked_once():
    """A compiled subgraph reuses its node's name and namespace when
    it starts, which fires a second ``on_chain_start`` for the same
    node. The gate must count that node once."""
    ran: list = []
    inner = StateGraph(_State)
    inner.add_node("leaf", _node("leaf", ran))
    inner.add_edge(START, "leaf")
    inner.add_edge("leaf", END)
    child = inner.compile(name="child")

    outer = StateGraph(_State)
    outer.add_node("child", child)
    outer.add_edge(START, "child")
    outer.add_edge("child", END)

    guard = LangGraphGuard(
        agent_id="pipe",
        contracts=["tool `child` must not be called more than once"],
        mode="enforce",
    )
    wrapped = guard.wrap_graph(outer.compile())
    out = wrapped.invoke({"log": []})
    assert out["log"] == ["leaf"]
    assert ran == ["leaf"]
    assert guard.violations == []


def test_non_execution_attributes_forward_to_inner_graph():
    ran: list = []
    graph = _pipeline(["parser", "forecaster"], ran)
    wrapped = _guard().wrap_graph(graph)
    assert set(wrapped.get_graph().nodes) == set(graph.get_graph().nodes)
