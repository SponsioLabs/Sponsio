"""LangGraph-friendly Sponsio entry points.

Usage::

    from sponsio.langgraph import Sponsio
    from sponsio import contract
    from langgraph.prebuilt import create_react_agent

    guard = Sponsio(
        agent_id="support_bot",
        contracts=[
            contract("refund needs policy check")
                .assume("called `issue_refund`")
                .enforce("must call `check_policy` before `issue_refund`"),
        ],
    )
    agent = create_react_agent(model, guard.wrap(tools))
"""

from __future__ import annotations

from typing import Any

from sponsio.core import Sponsio as _Sponsio
from sponsio.integrations.langgraph import LangGraphGuard, ToolCallBlocked


def Sponsio(**kwargs: Any) -> LangGraphGuard:  # noqa: N802 — branded factory
    """Create a LangGraph guard without passing ``framework="langgraph"``."""
    return _Sponsio(framework="langgraph", **kwargs)


__all__ = ["LangGraphGuard", "Sponsio", "ToolCallBlocked"]
