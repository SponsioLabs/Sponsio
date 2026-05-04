"""OpenAI Agents SDK-friendly Sponsio entry points.

Usage::

    from sponsio.agents import Sponsio
    from sponsio import contract
    from agents import Agent, Runner

    guard = Sponsio(
        agent_id="triage",
        contracts=[
            contract("no infinite handoffs")
                .guarantees("tool `handoff` at most 2 times"),
        ],
    )
    agent = Agent(name="triage", tools=guard.wrap(tools))
"""

from __future__ import annotations

from typing import Any

from sponsio.core import Sponsio as _Sponsio
from sponsio.integrations.agents import AgentsSDKGuard, ToolCallBlocked


def Sponsio(**kwargs: Any) -> AgentsSDKGuard:  # noqa: N802 — branded factory
    """Create an Agents SDK guard without passing ``framework="agents_sdk"``."""
    return _Sponsio(framework="agents_sdk", **kwargs)


__all__ = ["AgentsSDKGuard", "Sponsio", "ToolCallBlocked"]
