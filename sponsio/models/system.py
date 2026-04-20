"""System class -- the orchestrator supporting both Style B and Style C APIs."""

from __future__ import annotations

from sponsio.formulas.formula import Formula
from sponsio.models.agent import Agent
from sponsio.models.contract import Contract
from sponsio.patterns.library import DetFormula


class AgentBuilder:
    """Fluent builder for the Style C (chained) API.

    Accumulates assumptions and enforcements on an agent, then emits one
    ``Contract`` per enforcement — each paired with the same assumption
    set (ANDed). This matches the new per-contract semantics where each
    (A, E) pair is evaluated independently.
    """

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self._agent = Agent(id=agent_id)
        self._enforcements: list[Formula | DetFormula] = []
        self._assumptions: list[Formula | DetFormula] = []

    def enforces(self, *formulas: Formula | DetFormula) -> AgentBuilder:
        self._enforcements.extend(formulas)
        return self

    # Backward-compatible alias
    guarantees = enforces

    def assumes(self, *formulas: Formula | DetFormula) -> AgentBuilder:
        self._assumptions.extend(formulas)
        return self

    def tools(self, *tool_names: str) -> AgentBuilder:
        self._agent.tools.extend(tool_names)
        return self

    def permissions(self, *perms: str) -> AgentBuilder:
        self._agent.permissions.extend(perms)
        return self

    def to_contracts(self) -> list[Contract]:
        """Emit one Contract per enforcement, each gated by the full assumption set."""
        if not self._enforcements:
            return []
        assumption = (
            list(self._assumptions)
            if len(self._assumptions) > 1
            else (self._assumptions[0] if self._assumptions else None)
        )
        return [
            Contract(agent=self._agent, enforcement=e, assumption=assumption)
            for e in self._enforcements
        ]


class System:
    """Multi-agent system with contracts.

    Args:
        name: Human-readable name for this system.
        contracts: Optional pre-built contracts (Style B).
    """

    def __init__(self, name: str, contracts: list[Contract] | None = None) -> None:
        self.name = name
        self._contracts: list[Contract] = list(contracts) if contracts else []
        self._builders: dict[str, AgentBuilder] = {}

    def agent(self, agent_id: str) -> AgentBuilder:
        if agent_id not in self._builders:
            self._builders[agent_id] = AgentBuilder(agent_id)
        return self._builders[agent_id]

    @property
    def contracts(self) -> list[Contract]:
        """Resolves all contracts (Style B + Style C)."""
        return self._resolve_contracts()

    def _resolve_contracts(self) -> list[Contract]:
        result = list(self._contracts)
        existing_ids = {c.agent.id for c in result}
        for builder in self._builders.values():
            if builder.agent_id not in existing_ids:
                result.extend(builder.to_contracts())
        return result

    def __repr__(self) -> str:
        contracts = self._resolve_contracts()
        agents = len({c.agent.id for c in contracts})
        tools = sum(len(c.agent.tools) for c in contracts)
        return f"System({self.name!r}, {agents} agents, {tools} tools)"
