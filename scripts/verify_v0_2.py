"""Cross-integration verification for v0.2 enforcement-action surface.

Exercises every feature shipped in the v0.2 cycle against every
adapter that should support it, and prints a pass/fail report. Skips
gracefully when an adapter's underlying SDK isn't installed.

Features under test:

* ``tool_policy`` (YAML + inline, deny + allow + approved list)
* ``filter_tools`` per-turn proactive listing
* ``enforcement: proactive`` wrap-time static filter in
  LangGraph / CrewAI / OpenAI Agents SDK / Google ADK
* ``redirect_to_safe`` pattern + ``RedirectToSafe`` strategy
* LangGraph adapter's redirect dispatch (substitute call)
* ``EscalateToHuman(notify=[...])`` Slack-style side effect

Each adapter section prints PASS / FAIL / SKIP per check. Exit code 0
iff every check that ran passed. Run::

    python scripts/verify_v0_2.py

Use as a smoke test before publishing a release: any FAIL means
something in the v0.2 surface regressed.
"""

from __future__ import annotations

import sys
import traceback
from typing import Callable

from sponsio import contract
from sponsio.core import Sponsio
from sponsio.patterns import redirect_to_safe, tool_allowlist
from sponsio.runtime.strategies import EscalateToHuman


# ---------------------------------------------------------------------------
# Tiny test harness. avoids pulling pytest into the public verify script.
# ---------------------------------------------------------------------------

PASS = "\033[32m PASS \033[0m"
FAIL = "\033[31m FAIL \033[0m"
SKIP = "\033[33m SKIP \033[0m"

results: list[tuple[str, str, str]] = []


def check(label: str, fn: Callable[[], None]) -> None:
    """Run ``fn`` and record PASS / FAIL. ``fn`` may raise
    ``ImportError`` to skip (framework SDK missing)."""
    try:
        fn()
    except ImportError as e:
        results.append((SKIP, label, f"(SDK not installed: {e.name})"))
        return
    except Exception as e:
        tb = traceback.format_exc()
        results.append((FAIL, label, f"{type(e).__name__}: {e}\n{tb}"))
        return
    results.append((PASS, label, ""))


# ---------------------------------------------------------------------------
# Core (framework-free) checks. These exercise the runtime layer and
# must hold regardless of which adapter the user installs.
# ---------------------------------------------------------------------------


def core_tool_policy_inline_deny() -> None:
    guard = Sponsio(
        agent_id="bot",
        tool_policy={"default": "deny", "approved": ["search"]},
        mode="enforce",
        verbose=False,
    )
    assert not guard.guard_before("rm_rf", {}).allowed, "rm_rf should be blocked"
    assert guard.guard_before("search", {}).allowed, "search should pass"


def core_tool_policy_inline_allow() -> None:
    guard = Sponsio(
        agent_id="bot",
        tool_policy={"default": "allow"},
        mode="enforce",
        verbose=False,
    )
    assert guard.guard_before("rm_rf", {}).allowed, "allow mode shouldn't block"


def core_filter_tools_static() -> None:
    guard = Sponsio(
        agent_id="bot",
        tool_policy={"default": "deny", "approved": ["search", "read_file"]},
        mode="enforce",
        verbose=False,
    )
    legal = guard.filter_tools(["search", "rm_rf", "read_file", "drop_table"])
    assert legal == ["search", "read_file"], legal


def core_filter_tools_temporal() -> None:
    """``must_precede`` should open the dependent tool only after the
    precondition fires."""
    guard = Sponsio(
        agent_id="bot",
        contracts=["must call `check_policy` before `issue_refund`"],
        mode="enforce",
        verbose=False,
    )
    assert guard.filter_tools(["check_policy", "issue_refund"]) == ["check_policy"]
    guard.guard_before("check_policy", {})
    assert guard.filter_tools(["check_policy", "issue_refund"]) == [
        "check_policy",
        "issue_refund",
    ]


def core_filter_tools_is_pure() -> None:
    """Probe must not pollute log, perf samples, or trace."""
    guard = Sponsio(
        agent_id="bot",
        tool_policy={"default": "deny", "approved": ["search"]},
        mode="enforce",
        verbose=False,
    )
    log_before = len(guard._monitor.log)
    trace_before = len(guard._monitor._trace.events)
    perf_before = guard._monitor.performance_tracker.summarize().total_checks
    guard.filter_tools(["search", "rm_rf", "drop_table"])
    assert len(guard._monitor.log) == log_before, "log polluted"
    assert len(guard._monitor._trace.events) == trace_before, "trace polluted"
    assert guard._monitor.performance_tracker.summarize().total_checks == perf_before, (
        "perf polluted"
    )


def core_redirect_basic() -> None:
    guard = Sponsio(
        agent_id="bot",
        contracts=[
            contract("trash instead of rm").guarantees(
                redirect_to_safe("rm_rf", "trash")
            )
        ],
        mode="enforce",
        verbose=False,
    )
    r = guard.guard_before("rm_rf", {"path": "/tmp/x"})
    assert r.redirected, "should be redirected"
    assert r.redirected_to == "trash", r.redirected_to
    assert r.allowed, "redirected outcome leaves allowed=True for adapter"
    assert r.rollback_performed, "unsafe event should be rolled back"


def core_redirect_conditional() -> None:
    """Redirect only fires when the assumption activates."""
    guard = Sponsio(
        agent_id="bot",
        contracts=[
            contract("large refunds go to review")
            .assume("called `issue_refund`")
            .guarantees(redirect_to_safe("issue_refund", "log_refund_request"))
        ],
        mode="enforce",
        verbose=False,
    )
    # Other tools untouched.
    assert guard.guard_before("read_file", {}).allowed
    # First issue_refund triggers redirect on same step.
    r = guard.guard_before("issue_refund", {"amount": 50000})
    assert r.redirected
    assert r.redirected_to == "log_refund_request"


def core_escalate_notify_isolation() -> None:
    """A failing notifier must not crash enforce; the rest still fire."""
    seen: list[str] = []

    def broken(*_args):
        raise RuntimeError("slack outage")

    def healthy(*_args):
        seen.append("ok")

    formula = tool_allowlist(["search"])
    import warnings

    guard = Sponsio(
        agent_id="bot",
        contracts=[contract("only search").guarantees(formula)],
        policy={
            formula.desc: EscalateToHuman(reason="oncall", notify=[broken, healthy])
        },
        mode="enforce",
        verbose=False,
    )
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        r = guard.guard_before("rm_rf", {})
    assert any(v.action == "escalated" for v in r.det_violations)
    assert seen == ["ok"], "healthy notifier didn't fire after broken one raised"


# ---------------------------------------------------------------------------
# Adapter-specific checks. Each block skips if its SDK isn't installed.
# ---------------------------------------------------------------------------


def adapter_langgraph_proactive() -> None:
    from langchain_core.tools import tool as lc_tool

    from sponsio.integrations.langgraph import LangGraphGuard

    @lc_tool
    def search(q: str) -> str:
        """Search."""
        return q

    @lc_tool
    def rm_rf(path: str) -> str:
        """Delete."""
        return f"rm {path}"

    guard = LangGraphGuard(
        agent_id="bot",
        tool_policy={
            "default": "deny",
            "approved": ["search"],
            "enforcement": "proactive",
        },
        verbose=False,
    )
    node = guard.wrap([search, rm_rf])
    assert "search" in node.tools_by_name
    assert "rm_rf" not in node.tools_by_name, "rm_rf should be stripped at bind time"


def adapter_langgraph_redirect_dispatch() -> None:
    """LangGraph is the first adapter with full redirect dispatch.
    Verify the substituted tool actually runs."""
    from langchain_core.tools import tool as lc_tool

    from sponsio.integrations.langgraph import LangGraphGuard

    @lc_tool
    def rm_rf(path: str) -> str:
        """Hard delete."""
        return f"DELETED {path}"

    @lc_tool
    def trash(path: str) -> str:
        """Move to trash."""
        return f"TRASHED {path}"

    guard = LangGraphGuard(
        agent_id="bot",
        contracts=[
            contract("trash instead of rm").guarantees(
                redirect_to_safe("rm_rf", "trash")
            )
        ],
        # Adapter-level enforcement requires mode=enforce; the
        # production default is ``observe`` (shadow), where the
        # redirect would log but not actually substitute.
        mode="enforce",
        verbose=False,
    )
    node = guard.wrap([rm_rf, trash])
    # The model calls rm_rf; the wrapper substitutes trash.
    result = node.tools_by_name["rm_rf"].func(path="/tmp/x")
    assert "TRASHED" in result, f"redirect didn't fire: {result}"
    assert "DELETED" not in result, "unsafe tool actually ran!"


def adapter_langgraph_redirect_missing_safe() -> None:
    """An unknown safe target must raise loudly, not silently fall
    through to the unsafe tool."""
    from langchain_core.tools import tool as lc_tool

    from sponsio.integrations.langgraph import LangGraphGuard, ToolCallBlocked

    @lc_tool
    def rm_rf(path: str) -> str:
        """delete."""
        return f"DELETED {path}"

    guard = LangGraphGuard(
        agent_id="bot",
        contracts=[
            contract("redirect to missing").guarantees(
                redirect_to_safe("rm_rf", "trash_not_registered")
            )
        ],
        mode="enforce",
        verbose=False,
    )
    node = guard.wrap([rm_rf])
    try:
        node.tools_by_name["rm_rf"].func(path="/tmp/x")
    except ToolCallBlocked as e:
        assert "trash_not_registered" in str(e), e
        return
    raise AssertionError("expected ToolCallBlocked for missing safe target")


def adapter_crewai_proactive() -> None:
    from sponsio.integrations.crewai import CrewAIGuard

    def search(q: str) -> str:
        """Search for q."""
        return q

    def rm_rf(path: str) -> str:
        """Delete a path."""
        return path

    guard = CrewAIGuard(
        agent_id="bot",
        tool_policy={
            "default": "deny",
            "approved": ["search"],
            "enforcement": "proactive",
        },
        verbose=False,
    )
    wrapped = guard.wrap([search, rm_rf])
    names = {getattr(t, "name", getattr(t, "__name__", "")) for t in wrapped}
    assert "search" in names
    assert "rm_rf" not in names, f"rm_rf leaked into CrewAI bind: {names}"


def adapter_agents_sdk_proactive() -> None:
    from sponsio.integrations.agents import AgentsSDKGuard

    def search(q: str) -> str:
        return q

    def rm_rf(path: str) -> str:
        return path

    guard = AgentsSDKGuard(
        agent_id="bot",
        tool_policy={
            "default": "deny",
            "approved": ["search"],
            "enforcement": "proactive",
        },
        verbose=False,
    )
    wrapped = guard.wrap([search, rm_rf])
    names = {getattr(t, "name", getattr(t, "__name__", "")) for t in wrapped}
    assert "search" in names
    assert "rm_rf" not in names, f"rm_rf leaked into Agents SDK bind: {names}"


def adapter_google_adk_proactive() -> None:
    from sponsio.integrations.google_adk import GoogleADKGuard

    def search(q: str) -> str:
        return q

    def rm_rf(path: str) -> str:
        return path

    guard = GoogleADKGuard(
        agent_id="bot",
        tool_policy={
            "default": "deny",
            "approved": ["search"],
            "enforcement": "proactive",
        },
        verbose=False,
    )
    wrapped = guard.wrap([search, rm_rf])
    names = {getattr(t, "__name__", "") for t in wrapped}
    assert "search" in names
    assert "rm_rf" not in names, f"rm_rf leaked into Google ADK bind: {names}"


def adapter_reactive_blocks_at_call_time() -> None:
    """Across all wrap-based adapters: when ``enforcement: reactive``
    (the default), denied tools still appear in the bound list but are
    blocked at call time by ``guard_before``."""
    from langchain_core.tools import tool as lc_tool

    from sponsio.integrations.langgraph import LangGraphGuard, ToolCallBlocked

    @lc_tool
    def search(q: str) -> str:
        """Search."""
        return q

    @lc_tool
    def rm_rf(path: str) -> str:
        """Delete."""
        return path

    guard = LangGraphGuard(
        agent_id="bot",
        tool_policy={
            "default": "deny",
            "approved": ["search"],
            # enforcement defaults to reactive
        },
        mode="enforce",
        verbose=False,
    )
    node = guard.wrap([search, rm_rf])
    # Both bind under reactive.
    assert "rm_rf" in node.tools_by_name, "reactive shouldn't strip"
    # But rm_rf raises when called.
    try:
        node.tools_by_name["rm_rf"].func(path="/tmp/x")
    except ToolCallBlocked:
        return
    raise AssertionError("reactive guard didn't block rm_rf at call time")


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

CORE_CHECKS = [
    ("core: tool_policy inline deny", core_tool_policy_inline_deny),
    ("core: tool_policy inline allow", core_tool_policy_inline_allow),
    ("core: filter_tools static deny", core_filter_tools_static),
    ("core: filter_tools temporal opens", core_filter_tools_temporal),
    ("core: filter_tools is side-effect free", core_filter_tools_is_pure),
    ("core: redirect_to_safe basic", core_redirect_basic),
    ("core: redirect_to_safe conditional", core_redirect_conditional),
    ("core: escalate notify isolation", core_escalate_notify_isolation),
]

ADAPTER_CHECKS = [
    ("langgraph: proactive strips at wrap()", adapter_langgraph_proactive),
    ("langgraph: redirect dispatch substitutes", adapter_langgraph_redirect_dispatch),
    (
        "langgraph: redirect missing target fails",
        adapter_langgraph_redirect_missing_safe,
    ),
    ("langgraph: reactive blocks at call time", adapter_reactive_blocks_at_call_time),
    ("crewai: proactive strips at wrap()", adapter_crewai_proactive),
    ("agents_sdk: proactive strips at wrap()", adapter_agents_sdk_proactive),
    ("google_adk: proactive strips at wrap()", adapter_google_adk_proactive),
]


def main() -> int:
    print("\nSponsio v0.2 cross-integration verification\n" + "=" * 50)
    for label, fn in CORE_CHECKS + ADAPTER_CHECKS:
        check(label, fn)
    for status, label, note in results:
        line = f"{status} {label}"
        if note and status == FAIL:
            line += f"\n        {note.splitlines()[0]}"
        print(line)
    passed = sum(1 for s, *_ in results if s == PASS)
    failed = sum(1 for s, *_ in results if s == FAIL)
    skipped = sum(1 for s, *_ in results if s == SKIP)
    print("-" * 50)
    print(f"{passed} passed, {failed} failed, {skipped} skipped")
    if failed:
        print("\nFailure details:")
        for status, label, note in results:
            if status == FAIL:
                print(f"\n[{label}]\n{note}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
