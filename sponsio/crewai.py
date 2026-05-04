"""CrewAI-friendly Sponsio entry points.

Usage::

    from sponsio.crewai import Sponsio
    from sponsio import contract
    from crewai import Agent, Crew

    guard = Sponsio(
        agent_id="research_crew",
        contracts=[
            contract("only delegate to whitelisted agents")
                .assume("called `delegate`")
                .guarantees("delegate target in {researcher, writer}"),
        ],
    )
    crew = Crew(agents=guard.wrap(agents), tasks=tasks)
"""

from __future__ import annotations

from typing import Any

from sponsio.core import Sponsio as _Sponsio
from sponsio.integrations.crewai import CrewAIGuard


def Sponsio(**kwargs: Any) -> CrewAIGuard:  # noqa: N802 — branded factory
    """Create a CrewAI guard without passing ``framework="crewai"``."""
    return _Sponsio(framework="crewai", **kwargs)


__all__ = ["CrewAIGuard", "Sponsio"]
