"""Google ADK-friendly Sponsio entry points.

Usage::

    from sponsio.google_adk import Sponsio
    from google.adk.agents.llm_agent import Agent

    guard = Sponsio(config="sponsio.yaml", agent_id="travel")
    root_agent = Agent(name="travel", model="gemini-flash-latest", tools=guard.wrap(tools))
"""

from __future__ import annotations

from typing import Any

from sponsio.core import Sponsio as _Sponsio
from sponsio.integrations.google_adk import GoogleADKGuard


def Sponsio(**kwargs: Any) -> GoogleADKGuard:  # noqa: N802 - branded factory
    """Create a Google ADK guard without passing ``framework="google_adk"``."""
    return _Sponsio(framework="google_adk", **kwargs)


__all__ = ["GoogleADKGuard", "Sponsio"]
