"""Agent CRUD endpoints."""

from fastapi import APIRouter, HTTPException

from api.schemas import AgentCreate, AgentResponse
from api.state import state
from sponsio.models.agent import Agent

router = APIRouter()


@router.get("", response_model=list[AgentResponse])
def list_agents():
    return [
        AgentResponse(
            id=a.id,
            tools=a.tools,
            permissions=a.permissions,
            reads_from=a.reads_from,
            writes_to=a.writes_to,
        )
        for a in state.agents.values()
    ]


@router.post("", response_model=AgentResponse)
def create_agent(req: AgentCreate):
    if req.id in state.agents:
        raise HTTPException(400, f"Agent '{req.id}' already exists")
    agent = Agent(
        id=req.id,
        tools=req.tools,
        permissions=req.permissions,
        reads_from=req.reads_from,
        writes_to=req.writes_to,
    )
    state.agents[req.id] = agent
    return AgentResponse(
        id=agent.id,
        tools=agent.tools,
        permissions=agent.permissions,
        reads_from=agent.reads_from,
        writes_to=agent.writes_to,
    )


@router.delete("/{agent_id}")
def delete_agent(agent_id: str):
    if agent_id not in state.agents:
        raise HTTPException(404, f"Agent '{agent_id}' not found")
    del state.agents[agent_id]
    state.rebuild_monitor()
    return {"deleted": agent_id}
