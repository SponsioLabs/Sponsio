"""Agent dataclass representing a participant in a multi-agent system."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Agent:
    """An agent in a multi-agent system.

    Attributes:
        id: Unique identifier for the agent.
        tools: Tool names this agent is allowed to call.
        reads_from: Data store keys this agent reads from.
        writes_to: Data store keys this agent writes to.
        permissions: Permission labels held by this agent.
    """

    id: str
    tools: list[str] = field(default_factory=list)
    reads_from: list[str] = field(default_factory=list)
    writes_to: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)

    def __repr__(self) -> str:
        return f"Agent({self.id!r})"
