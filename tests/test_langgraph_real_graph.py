"""The LangGraph path, through a compiled graph.

`test_langgraph_integration.py` drives the guard directly. Nothing built a
`StateGraph`, compiled it, and invoked it, so the one thing a LangGraph
user actually does was never exercised: `guard.wrap(tools)` returns a
`ToolNode`, and a ToolNode outside a graph raises on a missing runtime
config before any contract is consulted.

Skipped when langgraph is absent, so the suite still runs on a bare
install.
"""

from __future__ import annotations

from typing import Annotated, TypedDict

import pytest

pytest.importorskip("langgraph")

from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.tools import tool  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402
from langgraph.graph.message import add_messages  # noqa: E402

from sponsio import contract  # noqa: E402
from sponsio.langgraph import Sponsio  # noqa: E402


@tool
def check_policy(order_id: str) -> str:
    """Check the refund policy for an order."""
    return "policy ok"


@tool
def issue_refund(order_id: str, amount: float) -> str:
    """Issue a refund for an order."""
    return f"refunded {amount}"


class _State(TypedDict):
    messages: Annotated[list, add_messages]


def _app(mode: str):
    guard = Sponsio(
        agent_id="my_bot",
        mode=mode,
        verbose=False,
        contracts=[
            contract("policy gate before refund")
            .assume("called `issue_refund`")
            .guarantees("must call `check_policy` before `issue_refund`")
        ],
    )
    graph = StateGraph(_State)
    graph.add_node("tools", guard.wrap([check_policy, issue_refund]))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    return graph.compile()


def _call(app, name: str, args: dict, idx: int) -> str:
    message = AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": f"c{idx}"}]
    )
    return str(app.invoke({"messages": [message]})["messages"][-1].content)


def test_a_compiled_graph_blocks_then_allows(monkeypatch) -> None:
    """The order the contract names, through the real node."""
    monkeypatch.setenv("SPONSIO_MODE", "enforce")
    app = _app("enforce")
    refund = {"order_id": "A1", "amount": 9.99}

    first = _call(app, "issue_refund", refund, 0)
    assert "ToolCallBlocked" in first or "BLOCKED" in first, first

    assert "policy ok" in _call(app, "check_policy", {"order_id": "A1"}, 1)

    after = _call(app, "issue_refund", refund, 2)
    assert "refunded" in after, after


def test_observe_mode_runs_the_tool(monkeypatch) -> None:
    """Observe records and does not stop. A mode that quietly enforced
    would make every first rollout a production incident.

    `SPONSIO_MODE` outranks the ctor arg by design, and conftest sets it to
    `enforce` for the suite, so this test has to clear it. See
    tests/test_mode_precedence.py."""
    monkeypatch.delenv("SPONSIO_MODE", raising=False)
    app = _app("observe")
    out = _call(app, "issue_refund", {"order_id": "A1", "amount": 9.99}, 0)
    assert "refunded" in out, out
