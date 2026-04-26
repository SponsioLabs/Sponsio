"""In-memory shared state for the API layer."""

from __future__ import annotations

import threading

from sponsio.models.agent import Agent
from sponsio.models.system import System
from sponsio.runtime.monitor import RuntimeMonitor
from sponsio.generation.nl_to_contract import build_contract


class AppState:
    """Holds the in-memory system, monitor, and agent registry.

    The external-spans list is mutated by FastAPI request handlers running
    on the thread pool (`api/routers/monitor.py` push-span and the SSE
    polling reader). Append + read on a Python list are not safe to
    interleave: a reader iterating the list while a writer appends can see
    duplicates / hole iterations. All access goes through the methods on
    this class so the lock is the single point of coordination.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.system = System("default")
        self.monitor = RuntimeMonitor(system=self.system)
        self.agents: dict[str, Agent] = {}
        self.active_demo: str = ""
        self._external_spans: list = []

    def rebuild_monitor(self) -> None:
        self.monitor = RuntimeMonitor(system=self.system)

    def clear_events(self) -> None:
        """Clear trace events and spans but keep contracts and agents intact."""
        with self.lock:
            self._external_spans = []
        self.rebuild_monitor()  # new monitor keeps existing system/contracts

    def reset(self) -> None:
        """Full reset: clears everything including contracts and agents."""
        with self.lock:
            self.system = System("default")
            self.agents.clear()
            self.active_demo = ""
            self._external_spans = []
        self.rebuild_monitor()

    # -----------------------------------------------------------------
    # External-spans accessors (thread-safe)
    # -----------------------------------------------------------------

    def append_external_span(self, span: dict) -> int:
        """Append an externally pushed span tree. Returns new total count."""
        with self.lock:
            self._external_spans.append(span)
            return len(self._external_spans)

    def external_spans(self) -> list[dict]:
        """Snapshot of external spans (safe to iterate without the lock)."""
        with self.lock:
            return list(self._external_spans)

    def clear_external_spans(self) -> None:
        with self.lock:
            self._external_spans = []

    def seed_demo(self, demo_id: str = "customer_service") -> None:
        """Pre-load a demo scenario (additive — preserves existing spans).

        Each call adds the scenario's agent and contracts to the current
        system without clearing spans from previous acts. This way a
        multi-act showcase can push all spans to one dashboard session.
        """
        self.active_demo = demo_id

        if demo_id == "customer_service":
            agent = Agent(
                id="customer_bot",
                tools=["lookup_order", "check_refund_policy", "issue_refund"],
            )
            self.agents["customer_bot"] = agent
            contract = build_contract(
                "tool `check_refund_policy` must precede `issue_refund`\n"
                "tool `issue_refund` must not be called more than once",
                agent,
            )
            self.system._contracts.append(contract)

        elif demo_id == "coding_agent":
            agent = Agent(
                id="coding_agent",
                tools=["execute_sql", "confirm_with_user", "check_db_environment"],
            )
            self.agents["coding_agent"] = agent
            contract = build_contract(
                "tool `confirm_with_user` must precede `execute_sql`\n"
                "tool `execute_sql` must not be called more than 2 times",
                agent,
            )
            self.system._contracts.append(contract)

        elif demo_id == "mcp_leak":
            agent = Agent(
                id="internal_agent",
                tools=["read_document", "slack_post"],
            )
            self.agents["internal_agent"] = agent
            contract = build_contract(
                "tool `read_document` must precede `slack_post`\n"
                "tool `slack_post` must not be called more than 3 times",
                agent,
            )
            self.system._contracts.append(contract)

        self.rebuild_monitor()


state = AppState()
