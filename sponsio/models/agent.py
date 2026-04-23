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

    def __post_init__(self) -> None:
        """Data-integrity checks (#16).

        ``Agent(id="")`` used to be silently accepted; downstream the id
        is used as the *key* in ``system.agents`` dicts, so an empty id
        collided with any other empty-id agent and per-agent contracts
        landed on the wrong object. Likewise, non-string entries in the
        tool / permission lists would serialize fine but fail the
        ``_called(tool)`` factory contract at the first evaluation.
        """
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError(
                f"Agent.id must be a non-empty string (got {self.id!r}). "
                "Empty ids collide in system.agents dict lookups."
            )
        for attr in ("tools", "reads_from", "writes_to", "permissions"):
            value = getattr(self, attr)
            if not isinstance(value, list):
                raise TypeError(
                    f"Agent.{attr} must be a list (got {type(value).__name__})."
                )
            for item in value:
                if not isinstance(item, str) or not item:
                    raise ValueError(
                        f"Agent.{attr} entries must be non-empty strings "
                        f"(got {item!r} in {value!r})."
                    )

    def __repr__(self) -> str:
        return f"Agent({self.id!r})"
