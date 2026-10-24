"""P3.2: the SDK's ``backend="daemon"``, and adapters that offer the model
only the tools the session's state allows, every turn.

The library ``bot`` says ``check`` comes before ``send``, and that once
``wipe`` has run, ``send`` is out. So the tool list grows after
``check`` and shrinks again after ``wipe``. Each framework test drives
that sequence through the framework's own per-turn hook and reads the
tools the model would have been offered.
"""

from __future__ import annotations

import asyncio
import tempfile
import types
from pathlib import Path
from typing import Any

import pytest

from sponsio.daemon.guard_service import GuardService
from sponsio.daemon.handlers import register_default_handlers
from sponsio.daemon.server import DaemonServer
from sponsio.guard_stdin import GuardPolicy
from sponsio.integrations.base import BaseGuard

TOOLS = ["check", "send", "wipe"]

BOT = """
version: "1"
agents:
  bot:
    contracts:
      - desc: "check before send"
        G:
          pattern: must_precede
          args: [check, send]
      - desc: "no send after wipe"
        G:
          pattern: no_reversal
          args: [wipe, send]
"""

LOCAL_CONTRACTS = [
    "tool `check` must precede `send`",
]


@pytest.fixture
def short_tmp() -> Path:
    d = Path(tempfile.mkdtemp(prefix="sp-turn-", dir="/tmp"))
    yield d
    for sub in sorted(d.rglob("*"), reverse=True):
        try:
            sub.rmdir() if sub.is_dir() else sub.unlink()
        except OSError:
            pass
    try:
        d.rmdir()
    except OSError:
        pass


@pytest.fixture
def daemon(short_tmp: Path):
    root = short_tmp / "plugins"
    (root / "bot").mkdir(parents=True)
    (root / "bot" / "sponsio.yaml").write_text(BOT)
    svc = GuardService(
        root=root,
        state_dir=short_tmp / "state",
        policy=GuardPolicy(mode="enforce", on_error="deny"),
    )
    server = DaemonServer(short_tmp / "sock", accept_timeout_s=0.05)
    register_default_handlers(server, guard_service=svc)
    server.start()
    try:
        yield svc, server.socket_path
    finally:
        server.stop(join_timeout_s=0.5)


def _remote(cls: type = BaseGuard, socket: Path | None = None, **kw: Any):
    return cls(
        agent_id="bot",
        backend="daemon",
        daemon_socket=socket,
        verbose=False,
        init_banner=False,
        auto_summary=False,
        **kw,
    )


def _run(guard: BaseGuard, tool: str) -> None:
    assert not guard.guard_before(tool, {}).stop_original


def _three_turns(guard: BaseGuard, offered) -> list[list[str]]:
    """What the model is offered before anything ran, after ``check``,
    and after ``wipe``."""
    seen = [offered()]
    _run(guard, "check")
    seen.append(offered())
    _run(guard, "wipe")
    seen.append(offered())
    return seen


EXPECTED = [["check", "wipe"], ["check", "send", "wipe"], ["check", "wipe"]]


# ---------------------------------------------------------------------------
# backend="daemon"
# ---------------------------------------------------------------------------


def test_the_daemon_decides_and_keeps_the_history(daemon):
    svc, socket = daemon
    guard = _remote(socket=socket)
    blocked = guard.guard_before("send", {"to": "x"})
    assert (
        blocked.stop_original
        and "check before send" in blocked.det_violations[0].message
    )
    _run(guard, "check")
    _run(guard, "send")
    events = svc.traces.events("bot", f"sdk:{guard._remote.session}")
    assert [e.tool for e in events] == ["check", "send"]
    # The process's own monitor holds nothing.
    assert guard._monitor.trace.events == []


def test_rules_cannot_be_passed_with_the_daemon_backend():
    with pytest.raises(ValueError, match="takes its rules from the daemon"):
        BaseGuard(agent_id="bot", backend="daemon", contracts=LOCAL_CONTRACTS)
    with pytest.raises(ValueError, match="backend must be"):
        BaseGuard(agent_id="bot", backend="cloud")


def test_no_daemon_blocks_everything(short_tmp):
    guard = _remote(socket=short_tmp / "absent.sock")
    check = guard.guard_before("check", {})
    assert check.stop_original
    assert "did not decide this call" in check.det_violations[0].message
    assert guard.filter_tools(TOOLS) == []
    guard.guard_after("check", "fine")  # never raises


def test_a_library_the_daemon_lacks_blocks(daemon):
    _svc, socket = daemon
    guard = BaseGuard(
        agent_id="nobody",
        backend="daemon",
        daemon_socket=socket,
        verbose=False,
        init_banner=False,
        auto_summary=False,
    )
    check = guard.guard_before("check", {})
    assert check.stop_original and "has no library `nobody`" in str(
        check.det_violations[0].message
    )
    assert guard.tools_for_turn(TOOLS) == []


def test_rotating_the_session_starts_an_empty_history(daemon):
    _svc, socket = daemon
    guard = _remote(socket=socket)
    _run(guard, "check")
    assert "send" in guard.tools_for_turn(TOOLS)
    guard.rotate_session(run_finish_session=False)
    assert guard.tools_for_turn(TOOLS) == ["check", "wipe"]


def test_observe_mode_in_the_daemon_offers_everything(daemon):
    svc, socket = daemon
    svc._policy = GuardPolicy(mode="observe", on_error="deny")
    guard = _remote(socket=socket)
    assert guard.tools_for_turn(TOOLS) == TOOLS
    assert not guard.guard_before("send", {}).stop_original


def test_local_reactive_guards_offer_everything():
    guard = BaseGuard(
        agent_id="bot", contracts=LOCAL_CONTRACTS, verbose=False, init_banner=False
    )
    assert guard.tools_for_turn(TOOLS) == TOOLS


def test_local_proactive_guards_filter_per_turn():
    guard = BaseGuard(
        agent_id="bot",
        contracts=LOCAL_CONTRACTS,
        tool_policy={"enforcement": "proactive"},
        mode="enforce",
        env_mode=False,
        verbose=False,
        init_banner=False,
    )
    assert guard.tools_for_turn(TOOLS) == ["check", "wipe"]
    _run(guard, "check")
    assert guard.tools_for_turn(TOOLS) == TOOLS


# ---------------------------------------------------------------------------
# The list shrinks after a state change, in every framework
# ---------------------------------------------------------------------------


def test_openai_tools_follow_the_state(daemon):
    from sponsio.integrations.openai import OpenAIGuard

    _svc, socket = daemon
    guard = _remote(OpenAIGuard, socket)
    sent: list[dict] = []

    class Completions:
        def create(self, **kwargs: Any) -> Any:
            sent.append(kwargs)
            message = types.SimpleNamespace(
                role="assistant", content="ok", tool_calls=None
            )
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=message, finish_reason="stop")]
            )

    client = guard.wrap(
        types.SimpleNamespace(chat=types.SimpleNamespace(completions=Completions()))
    )
    tools = [
        {"type": "function", "function": {"name": n, "parameters": {}}} for n in TOOLS
    ]

    def offered() -> list[str]:
        client.chat.completions.create(model="m", messages=[], tools=tools)
        return [t["function"]["name"] for t in sent[-1].get("tools", [])]

    assert _three_turns(guard, offered) == EXPECTED
    assert len(tools) == 3  # the caller's list is not changed


def test_openai_drops_tools_and_tool_choice_when_nothing_is_offered(short_tmp):
    from sponsio.integrations.openai import _offer_tools_for_turn

    guard = _remote(socket=short_tmp / "absent.sock")
    kwargs = {
        "tools": [{"type": "function", "function": {"name": "check"}}],
        "tool_choice": "required",
    }
    _offer_tools_for_turn(guard, "chat", kwargs)
    assert "tools" not in kwargs and "tool_choice" not in kwargs


def test_langgraph_dynamic_model_follows_the_state(daemon):
    from sponsio.integrations.langgraph import LangGraphGuard

    _svc, socket = daemon
    guard = _remote(LangGraphGuard, socket)
    bound: list[list[str]] = []

    class Model:
        def bind_tools(self, tools: list) -> str:
            bound.append([t.__name__ for t in tools])
            return "bound"

    def check(): ...
    def send(): ...
    def wipe(): ...

    select = guard.dynamic_model(Model(), [check, send, wipe])

    def offered() -> list[str]:
        assert select({"messages": []}, None) == "bound"
        return bound[-1]

    assert _three_turns(guard, offered) == EXPECTED


def test_langgraph_dynamic_model_in_create_react_agent(daemon):
    pytest.importorskip("langgraph.prebuilt")
    from langchain_core.language_models.fake_chat_models import (
        FakeMessagesListChatModel,
    )
    from langchain_core.messages import AIMessage
    from langchain_core.tools import tool
    from langgraph.prebuilt import create_react_agent

    from sponsio.integrations.langgraph import LangGraphGuard

    _svc, socket = daemon
    guard = _remote(LangGraphGuard, socket)
    bound: list[list[str]] = []

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools: Any, **kw: Any) -> Any:
            bound.append(sorted(t.name for t in tools))
            return self

    @tool
    def check() -> str:
        """Check."""
        return "checked"

    @tool
    def send() -> str:
        """Send."""
        return "sent"

    @tool
    def wipe() -> str:
        """Wipe."""
        return "wiped"

    model = Model(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "check", "args": {}, "id": "1"}],
            ),
            AIMessage(content="done"),
        ]
    )
    tools = [check, send, wipe]
    agent = create_react_agent(guard.dynamic_model(model, tools), guard.wrap(tools))
    agent.invoke({"messages": [("user", "go")]})
    assert bound == [["check", "wipe"], ["check", "send", "wipe"]]


def test_google_adk_request_follows_the_state(daemon):
    from sponsio.integrations.google_adk import GoogleADKGuard

    _svc, socket = daemon
    guard = _remote(GoogleADKGuard, socket)

    def offered() -> list[str]:
        decl = [types.SimpleNamespace(name=n) for n in TOOLS]
        request = types.SimpleNamespace(
            tools_dict={n: object() for n in TOOLS},
            config=types.SimpleNamespace(
                tools=[types.SimpleNamespace(function_declarations=decl)]
            ),
        )
        assert guard.before_model_callback(None, request) is None
        declared = [
            d.name for t in request.config.tools for d in t.function_declarations
        ]
        assert declared == list(request.tools_dict)
        return declared

    assert _three_turns(guard, offered) == EXPECTED


def test_openai_agents_sdk_is_enabled_follows_the_state(daemon):
    from dataclasses import dataclass

    from sponsio.integrations.agents import AgentsSDKGuard

    _svc, socket = daemon
    guard = _remote(AgentsSDKGuard, socket)

    @dataclass
    class FunctionTool:
        name: str
        is_enabled: Any = True

    gated = [guard._enabled_per_turn(FunctionTool(n)) for n in TOOLS]

    def offered() -> list[str]:
        return [t.name for t in gated if asyncio.run(t.is_enabled(None, None))]

    assert _three_turns(guard, offered) == EXPECTED
    # The tool's own switch still counts.
    off = guard._enabled_per_turn(FunctionTool("check", is_enabled=False))
    assert asyncio.run(off.is_enabled(None, None)) is False


def test_claude_agent_query_options_follow_the_state(daemon):
    from sponsio.integrations.claude_agent import ClaudeAgentGuard

    _svc, socket = daemon
    guard = _remote(ClaudeAgentGuard, socket)

    def offered() -> list[str]:
        opts = guard.query_options(TOOLS)
        assert sorted(opts["allowed_tools"] + opts["disallowed_tools"]) == TOOLS
        return opts["allowed_tools"]

    assert _three_turns(guard, offered) == EXPECTED
